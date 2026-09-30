# S3 마운트 연결 관리

## 범위와 기준
- 2026-09-29 사용자 첨부 계획을 실행한다. Python 3.12 / PySide6 / SQLite 구조를 유지한다.
- 저장 연결 CRUD, Bucket/Prefix→드라이브/내부 Prefix, 공통 인증, 다중 연결,
  실제 드라이브 상태, 캐시 보호, 트레이/종료, 프로필 삭제, CLI, 진단/배포를 구현한다.
- 자동 연결, Windows Service, 독립 백그라운드 서비스는 비목표다.
- 탐색기 쓰기/삭제는 실제 IAM 권한을 따른다. 기존 S3 파일 탭의 확인 정책과 구분한다.

## 설계 결정
- rclone + WinFsp: 기존 VFS/캐시/가상 폴더 지원을 사용한다. WinFsp 직접 구현은
  파일시스템/복구 로직 비용 때문에 채택하지 않는다.
- 프로필 인증은 SessionGuard 및 AuthenticatedOperationCoordinator를 재사용한다.
- 별도 드라이브 테이블은 기존 S3Location 검증을 재사용한다. 기존 파일 탭 저장 위치와
  생명주기는 다르므로 위치 레코드를 암묵적으로 만들거나 삭제하지 않는다.
- 미전송/불명확한 캐시를 보존하며 편집·삭제·프로필 삭제·완전 종료를 막는다.
- CLI 연결도 전경 프로세스가 소유한다. 연결을 유지하는 동안 명령을 받을 수 있어야 한다.

## 순서 / 수용 기준
- [ ] 제품/보안/신뢰성/UI 계약 갱신
- [ ] Domain/Application/SQLite 및 fake tests
- [ ] Windows/rclone 어댑터 및 경계 tests
- [ ] CLI 기능 검증 후 GUI 연결
- [ ] 트레이/종료/프로필 삭제 통합
- [ ] 진단/배포/문서 생성
- [ ] 전체 tests, Ruff/mypy/architecture/security, scripts/check.ps1
- [ ] 승인된 비운영 AWS/WinFsp 실환경 검증

## 검증과 제약
- 초기 `graft map`, `rg --files`, `git status --short`, 아키텍처/기존 active plan 확인.
- 기존 작업 트리에 active 001, README, 테스트 임시 파일 변경이 있다. 본 작업과 구분한다.
- 실환경 대상은 사용자 지정 비운영 프로필/Bucket이며 채팅에 제공되었다.
  조회·연결·해제만 승인됨. 파일 생성·수정·삭제는 금지한다. 실제 식별자는 저장소에 기록하지 않는다.
- 명령/정확한 결과는 구현 단계별 아래 기록한다.

## 런타임 영향
연결당 자식 프로세스, 로컬 제어 통신, Windows 드라이브 점유, 보호된 디스크 캐시,
SQLite migration, 상태 폴링이 추가된다. 앱 완전 종료 시 안전 정리 실패는 종료를 차단한다.
캐시는 자동 삭제하지 않으며, rclone/WinFsp 설치는 별도 배포 전제다.

## 진행 기록
- UI 정합성 후속: RDS `_field`를 `form_fields.form_field`로 추출하고 기존 import 별칭 유지.
  공통 compact 목록, 카드/필드/버튼 QSS, page_heading/apply_page_layout 재사용.
  모달 편집을 인라인 편집으로 교체, 검색/경로 미리보기/미저장 보호/연결 중 잠금 구현.
  첨부의 자동 연결/접근 모드는 기능 요구로 해석하지 않음. DESIGN 및 HTML 목업 동기화.
- `.venv/Scripts/python.exe -m pytest tests/adapter/gui/test_s3_drives.py tests/adapter/gui/test_ec2_rds.py tests/adapter/gui/test_shared_style.py tests/adapter/gui/test_shell.py --no-cov -q --basetemp=.test-drive-ui -o cache_dir=.test-cache-drives` → 59 passed.
- 모의 서비스 Qt 렌더 `.qa-s3-drives/desktop.png`, `small.png` 확인. 페이지 크기
  1204×802/804×628에서 버튼 유지, 최소 크기는 필드 스크롤. 실제 AWS 호출 없음.
- UI 부수효과: 기존 3초 상태 조회 유지, 문자 조회는 프로필/새 연결 진입 시 worker에서 실행.
  프로세스/포트/DB/인증 계약 변경 없음. 편집 중 상태 갱신은 입력을 덮어쓰지 않음.
- UI 최종 `ruff check src tests tools`, `mypy`(95 files), `tools/architecture_check.py` 통과.
  `scripts/check.ps1`은 기존 documentation 링크 오류로 실패(.qa-s3-drives/check.log).
  전체 배포 게이트 완료로 보고하지 않는다. 앱 재시작으로 UI 반영, EXE는 재빌드 필요.
- 후속 사용자 명시 승인 후 WinFsp Core 설치 완료. MSI의 실제 feature는
  F.Main,F.User이며 Core는 표시 이름이다. Windows UAC를 거쳐 installer exit 0 확인.
- 실마운트에서 rclone의 `--config nul` 거부를 발견해 current-user ACL 보호된 빈
  설정 파일로 수정했다. 인증정보는 파일에 기록하지 않는다.
- WinFsp unmount 응답과 실제 드라이브 문자 해제의 시간차를 발견해 최대 10초 대기 추가.
- 승인된 비운영 대상 read-only smoke: connected, root listing accessible=True,
  uploadsQueued/uploadsInProgress/erroredFiles=0, core/quit 성공, unmount confirmed=True.
  파일 생성·수정·삭제 없음. 임시 드라이브와 프로세스 정리 완료.
- `.venv/Scripts/python.exe -m pytest tests/unit/infrastructure/test_rclone_drive_mount.py tests/unit/infrastructure/test_drive_runtime.py tests/unit/application/test_s3_drives.py tests/adapter/cli/test_s3_drives_cli.py --no-cov -q --basetemp=.test-drive-installed -o cache_dir=.test-cache-drives` → 31 passed.
  `mypy` 94 files passed, `ruff check src tests tools` passed.
  설치/시작/해제 결함은 해결됨. 전체 최초 계획의 나머지 검증은 완료로 표시하지 않는다.
- Domain/Application/SQLite migration 12, rclone adapter, CLI 전경 세션,
  GUI 탭/트레이/삭제 lifecycle의 구현이 작업 트리에 추가되었다. 전체 기능 완료로 주장하지 않는다.
- 중단 시 runtime ZIP/MSI 다운로드만 완료되어 rclone.exe와 WinFsp가 없었다.
- 2026-09-29 재개: 공식 SHA256SUMS와 ZIP 일치 확인, rclone 1.75.1 압축 해제 및 실행 성공.
  setup-drive-runtime.ps1로 재현 가능하게 하고 build-package.ps1에 checksum 검증/바이너리 포함 추가.
- WinFsp registry 32/64 view 탐지, rclone/WinFsp 별도 오류 안내 구현.
- WinFsp 2.1.25156 MSI의 upstream SHA256 일치 및 Authenticode Valid 확인.
  설치는 자동 승인 검토가 거부했다: 시스템 커널 드라이버 설치의 명시적 사용자 승인 부재.
  설치 우회하지 않았으며 실마운트 검증은 아직 미실행이다.
- `.venv/Scripts/python.exe -m pytest tests/unit/application/test_s3_drives.py tests/unit/infrastructure/test_rclone_drive_mount.py tests/adapter/cli/test_s3_drives_cli.py tests/unit/application/test_doctor_service.py tests/adapter/gui/test_shell.py --no-cov -q --basetemp=.test-drive-runtime-fix -o cache_dir=.test-cache-drives` → 55 passed.
- `.venv/Scripts/python.exe -m pytest tests/unit/infrastructure/test_drive_runtime.py --no-cov -q --basetemp=.test-runtime-probes -o cache_dir=.test-cache-drives` → 2 passed.
- `.venv/Scripts/python.exe -m mypy` → 94 files passed.
- `scripts/check.ps1` → 기존 .vendor-license-source/graft 문서 링크 오류로 documentation 실패.
  전체 게이트 통과 아님. `UV_CACHE_DIR=.uv-cache` 설정 후 `scripts/generate-docs.ps1` 성공.
- `tools/architecture_check.py` 통과. 현재 runtime probe는 rclone_exists=True,
  winfsp_available=False. rclone 실행 확인만 완료했고 Windows/S3 마운트 성공 주장은 하지 않는다.
- 당시 다음 단계는 WinFsp 설치 승인 및 read-only smoke였으며, 위 후속 기록대로 완료했다.
  최초 계획 전체의 문서/캐시/배포 게이트는 여전히 별도 검증이 필요하다.


## 2026-09-30 S3 마운트 UX 요청 완료
- SSOT 검색: `graft ask "S3Service list_buckets S3DrivesPage RdsPage clone menu" --source`,
  `rg`로 list_buckets/cached_buckets, action_button/common-start, menu_action,
  QDesktopServices, 기존 드라이브 상태/저장 경계를 확인했다.
- 화면 명칭 S3 마운트, 동일 너비의 S3/Windows 위치, 한 줄 최종 경로/상태,
  저장/연결/해제 아이콘 및 Tooltip, 연결 중 입력 잠금/해제 후 값 유지 적용.
- 기존 S3Service의 성공 Bucket 조회를 프로필/리전별 메모리 캐시에 보관하고
  S3 파일/마운트가 공유한다. 마운트 진입 시 캐시 우선, 없으면 자동 조회,
  수동 갱신은 새 조회. 프로필 전환 시 이전 목록 제거와 늦은 응답 차단.
- S3 파일의 메뉴 위젯을 menu_actions로 추출해 재사용. 행 메뉴의 이름 수정,
  복제, 위험 색상/확인 삭제. 복제는 OS와 저장 목록의 미사용 문자를 재검증한다.
- 탐색기는 Application에서 연결 상태/최종 경로 확인 후 열며, 신규/변경 설정의
  연결은 저장 후 수행한다. MFA 취소 후 재조회와 종료 중 저장 완료 경합도 검증.
- DESIGN 및 HTML 목업 갱신. 새 라이브러리/DB migration 없음.
- 부수효과: worker에서 화면 진입 시 Bucket 조회가 발생할 수 있으며 메모리 캐시가
  프로필별로 유지된다. 사용자 클릭 시 Windows 탐색기를 연다. 기존 마운트 프로세스,
  포트/캐시 계약은 변경하지 않았고 실제 AWS 작업은 이번 검증에서 수행하지 않았다.

### 검증 명령/결과
- `.venv/Scripts/python.exe -m pytest tests/adapter/gui tests/unit/application/test_s3_drives.py tests/unit/application/test_s3_service.py tests/adapter/cli/test_s3_drives_cli.py tests/unit/infrastructure/test_rclone_drive_mount.py --no-cov -q --basetemp=.test-mount-regression -o cache_dir=.test-cache-mount` → 190 passed, 1 skipped.
- `.venv/Scripts/python.exe -m pytest tests/adapter/test_error_mapper_completeness.py tests/adapter/test_presentation_consistency.py --no-cov -q --basetemp=.test-mount-contract -o cache_dir=.test-cache-mount` → 3 passed.
- MFA 취소/종료 경합 테스트 추가 후 `.venv/Scripts/python.exe -m pytest tests/adapter/gui/test_s3_drives.py --no-cov -q --basetemp=.test-mount-final -o cache_dir=.test-cache-mount` → 12 passed.
- `.venv/Scripts/python.exe -m ruff check src tests tools` → passed.
- `.venv/Scripts/python.exe -m ruff format --check` + 이번 변경 Python 10개 파일 → passed.
- `.venv/Scripts/python.exe -m mypy` → 96 files passed.
- `.venv/Scripts/python.exe tools/architecture_check.py` → passed;
  `.venv/Scripts/lint-imports.exe` → 2 contracts kept.
- `.venv/Scripts/python.exe -m bandit -q -r src` → exit 0.
- `.venv/Scripts/detect-secrets.exe scan` + src/tests/tools의 Python 파일 목록 → 0 files flagged.
- `.venv/Scripts/python.exe .test-drive-runtime/render-ui.py` → 1204×802/804×628
  Qt offscreen 렌더 확인. `.qa-s3-drives/desktop.png`, `small.png`.
- `graft build` → success (로컬 그래프 갱신).
- `./scripts/check.ps1` → 기존 .vendor-license-source/graft 링크 오류로 documentation 실패.
  로그 `.qa-s3-drives/check-mount.log`; 전체 배포 게이트 완료로 주장하지 않는다.
- 전체 `ruff format --check src tests tools`는 기존 authentication_service.py, ports.py,
  aws_identity_gateway.py, test_sqlite_profile_store.py의 혼합 줄바꿈 문제로 실패.
  요청 범위 밖 파일은 변경하지 않았다(QA-001).
- 다음 단계: 개발 앱 재시작으로 UI 확인. 배포본 반영은 기존 패키지 빌드 절차로 재빌드.
  최초 계획의 남은 배포/전체 문서 게이트는 QA-001 및 기존 배포 부채에 따라 진행한다.


## 2026-09-30 RDS 백그라운드/트레이 종료 후속 요청
- 수용 기준 완료: RDS 연결 시 별도 콘솔 없음, X로 트레이 이동 시 유지,
  완전 종료 시 프로필과 무관하게 소유 터널 전체 정리, 연결 시작 중 종료 경합 차단.
- SSOT 확인: graft ask/skeleton 및 rg의 `external_terminal`, `Popen`, `creationflags`,
  `shutdown`, `stop_all`, `PORT_FORWARD_DOCUMENT`. 기존 managed runner와 GUI 종료 재사용.
- 선택: 기존 프로세스 어댑터 숨김 실행(추천: 종료/인증 계약 재사용, 변경 작음).
  대안: 독립 Windows 서비스(앱 밖 유지 가능하나 별도 설치/소유권 복잡성이 불필요).
- 포트 포워딩 문서 상수를 ports로 옮겨 Application/adapter가 공유한다.
  SessionManagerPlugin에 주입 가능한 background factory 추가. Windows CREATE_NO_WINDOW,
  DEVNULL stdio 적용. 세션 실패 관측/오류 매핑/SSM 정리는 기존 runner를 사용한다.
- RDS 페이지는 완전 종료 요청 이후 시작을 막고 시작 결과를 기다린 후 stop_all을 실행한다.
  MFA 대기 결과는 종료 중에는 취소 처리한다. X/완전 종료 분기 테스트 추가.
- 부수효과: RDS 자식 프로세스의 콘솔/표준 입출력만 변경하며 기존 포트·AWS 권한·DB 유지.
  EC2 대화형 터미널 유지. 실제 AWS/RDS 연결은 이번 검증에서 실행하지 않았다.
- `.venv/Scripts/python.exe -m pytest tests/unit/infrastructure/test_session_manager_plugin.py tests/unit/application/test_managed_ssm_session.py tests/unit/application/test_rds_tunnel_service.py tests/adapter/gui/test_ec2_rds.py tests/adapter/cli/test_rds_cli.py --no-cov -q --basetemp=.test-rds-background -o cache_dir=.test-cache-rds-bg` → 79 passed.
- `.venv/Scripts/python.exe -m pytest tests/adapter/gui/test_shell.py tests/adapter/gui/test_s3_drives.py tests/unit/test_session_host.py tests/adapter/test_error_mapper_completeness.py --no-cov -q --basetemp=.test-rds-bg-shell -o cache_dir=.test-cache-rds-bg` → 42 passed.
- 실제 `_start_background_process`로 Python 자식 실행 후 Win32 `GetConsoleWindow() == 0`
  검사 → exit 0, Windows hidden-process smoke PASS. AWS 작업 없음.
- `ruff check src tests tools` passed, `mypy` 96 files passed,
  `tools/architecture_check.py` passed, `bandit -q -r src` exit 0 (기존 nosec 주석 경고 존재).
- `./scripts/check.ps1` → documentation 실패(기존 QA-001),
  `.qa-s3-drives/check-rds-background.log`. 전체 배포 검증 통과로 주장하지 않는다.
- DESIGN/RELIABILITY 갱신. 다음 단계: 앱 재시작으로 반영, EXE 배포 시 재빌드.
  기존 전체 문서/배포 게이트는 QA-001 및 최초 계획의 잔여 항목으로 남긴다.
