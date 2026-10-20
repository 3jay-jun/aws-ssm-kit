# 한국어 작업 기록 1.0.3 배포

## 목표와 범위
- 사용자 요청: 한국어 작업 기록 변경을 커밋하고 다운로드 가능한 배포 후보까지 배포한다.
- source: codex/korean-history-release, base origin/main 96e00af.
- 별도 EC2 연결 종료 알림 작업(007)은 이번 변경에서 제외하고 원래 작업 폴더에 보존한다.
- 정식 GitHub Release는 기존 [배포 정책](../../../packaging/README.md)의 Windows 10/11
  수동 VM 증거 및 보호된 승인 절차를 유지한다. 증거를 날조하거나 게이트를 우회하지 않는다.

## 수용 기준
- [x] 언어/의존성 확인: Python 3.12, PySide6 6.11.2, 기존 잠금 버전 유지.
- [x] 원격 main을 fetch하여 기준과 일치 확인. 별도 worktree로 한국어 변경만 분리.
- [x] pre-landing/coverage/plan 독립 감사 후 발견된 코드·검사 결함 보완.
- [ ] 배포 브랜치 전체 check.ps1 및 production ZIP/Setup smoke 통과.
- [ ] 한국어 표시 변경과 1.0.3 버전/변경기록을 논리적 커밋으로 나눔.
- [ ] 원격 push/PR/CI 확인 및 main에 병합해 배포 workflow 실행.
- [ ] 원격 production 후보 ZIP/Setup/checksum 생성 성공을 확인해 다운로드 경로 전달.

## 결정·대안 및 영향
- 버전 SSOT는 pyproject.toml이며 repository의 3자리 버전을 유지해 1.0.2 → 1.0.3.
  스킬의 VERSION/package.json/4자리 형식은 이 Python 배포 형식에 적용하지 않는다.
- PATCH 추천: 기존 UX 버그 수정이며 API/DB 변경 없음. MINOR 대안은 기능/호환성 변화가 없어 부적절.
- GitHub main push의 기존 release workflow는 공개 Release 대신 검증용 배포 후보를 만든다.
- 제품 실행 시 번역 카탈로그 외 새 프로세스·포트·AWS 요청·DB 이관 없음.
  개발 작업은 새 가상환경·빌드·GitHub 네트워크 및 원격 CI를 사용한다.

## 검토와 보완
- SSOT 검색: graft map 및 logged_operation/record_success/record_failure/message_code,
  기존 scripts/build-package.ps1, build-installer.ps1, .github/workflows/release.yml.
- ship 스킬 사용. 설치된 checklist 누락을 확인하고 공식 garrytan/gstack upstream의
  review/checklist.md를 읽어 두 pass 검토했다. 스킬의 명시적 지시에 따라 독립 감사를 위임했다.
- SQLite GUI 오류는 technical cause가 message에, 원래 code가 action에 남는 경로를 확인했다.
  공통 log_message_code가 알려진 code를 복원하며 복사 시 원래 기술 원인을 유지한다.
- 새 작업명 검사는 일반 feature fallback도 통과하던 문제를 정확한 registry 등록 검사로 변경했다.
  미등록 EC2 작업 negative probe, 특수 문구/일반 실패/RDS 일반 종료 테스트를 보완했다.
- 독립 재검토: 잔여 결함 없음. coverage 감사: 검토 경로 35/35에 테스트 있음
  (AI 소스 감사이며 측정 coverage와 구분). plan/실제 소스 대조 완료.

## 실행 증거
- cache 미설정 uv 실행과 잘못된 PathInfo.Parent 계산은 cache 접근 거부로 실패했다.
  절대 workspace cache와 새 .venv-release를 지정했다. 샌드박스가 Windows PE 리소스 생성을
  차단해 가상환경 생성만 권한 확장으로 수행했다. 기존 사용자 환경은 수정하지 않았다.
- uv lock --offline 및 uv sync --locked --offline 성공. 의존성 변경 없이 로컬 앱만 1.0.3.
- import 경로가 isolated worktree/src/aws_connect임을 확인했다.
- 리뷰 수정 집중 검사: 관련 GUI/catalog/SQLite 통합 테스트 82 passed.
- `. ./.ship-env.ps1; ./scripts/check.ps1 *> .ship-check.log` → exit 0, **All local checks passed**.
  전체 729 passed/1 skipped, 문서/포맷/lint/경계/타입/critical coverage/secret scan/Bandit/audit 통과.
- 최종 상세 복사 보존 수정 후 `uv run --no-sync pytest --basetemp=.test-ship-final -o cache_dir=.test-cache-ship-final`
  → **729 passed, 1 skipped, coverage 87.00%**, `.ship-tests-final.log`.
  `uv run --no-sync mypy` → 101 files passed, critical coverage 5 files 100%.
- 변경·신규 source/test/계획 파일을 명시한 `detect-secrets scan` → **0**, `.ship-secrets.json`.
- `./scripts/build-package.ps1 -VendorDirectory ./vendor/session-manager-plugin` → exit 0,
  production ZIP **aws-connect-1.0.3-windows-x64.zip**, SHA-256
  `a6d0fb94d8c223620ca2fac32534ce83124fdeb1901dc1cd9491a4c943844c81`.
- 빌드 도중 마지막 표시 수정이 있어 PyInstaller PYZ의 log_messages/logs/errors/view_models
  code object를 최종 소스 compile 결과와 비교했다. 바이트코드·상수·이름·인자/closure 정의 모두 일치.
- `./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-1.0.3-windows-x64.zip -ExpectedWinFspState ready`
  → exit 0, `.ship-package-smoke.log`. 한글/공백 경로·읽기 전용 앱·격리 LOCALAPPDATA·GUI 및 3개 EXE
  실행과 DB/log 민감정보 검증 통과. 실제 AWS 요청 및 사용자 데이터 접근 없음.
- native Git 자격 증명 조회는 대화형 로그인을 요구해 작업 소유 조회 프로세스만 종료했다.
  연결된 GitHub 앱은 저장소 admin/push 권한을 확인했으며 원격 반영에 이를 사용한다.
  토큰을 파일·출력에 기록하지 않았다. 별도 GitHub 인증을 요구하지 않는다.

## 다음 단계
최종 소스의 전체 검사 및 ZIP/Setup 빌드를 검증하고 커밋·원격 배포를 진행한다.
