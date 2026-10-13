# 통합 설치형 및 포터블 ZIP 배포 실행 계획

- 작성일: 2026-10-13 (Asia/Seoul)
- 상태: 사용자 지정 범위인 기능 구현·로컬 후보·검증 안내 완료. 깨끗한 VM 검증·원격 CI·정식 공개는 외부 게이트.
- 근거: 사용자의 설치형/ZIP 동시 제공 요청과 plan-eng-review의 D1~D6 선택.

## 1. 목표와 확정 범위

같은 앱 빌드에서 설치형과 ZIP을 제공한다. 처음 사용하는 PC에서도 사용자가
필수 프로그램을 별도로 검색하지 않고 S3 마운트를 준비할 수 있어야 한다.

| 배포물 | 구성 및 사용자 흐름 |
|---|---|
| Setup.exe | 설치 경로 선택 → 앱/rclone 배치 → 필요한 경우 WinFsp 설치 → 바로가기 생성 |
| 포터블 ZIP | 압축 해제 → 일반 권한으로 앱 실행 → 필요하면 앱에서 WinFsp 설치 |

- 설치형은 PC 전체 설치. 기본 경로는 `C:\Program Files\AWS-SSM-KIT`, C/D 등 다른 경로 선택 허용.
- 시작 메뉴 바로가기는 생성하고, 바탕화면 바로가기는 기본 선택된 옵션으로 제공한다.
- 두 배포물에 rclone 실행 파일과 WinFsp 공식 MSI를 포함한다. 사용자 PC의 설치 과정은 오프라인 가능.
- WinFsp 설치 취소/실패 시에도 앱 설치는 완료한다. 기본 기능은 사용 가능하고 S3 마운트만 제한한다.
- 앱 내 설치/재시도와 기존 WinFsp 교체/복구 지원까지 포함한다(D1: 기존 범위 유지).
- 일반 앱/CLI는 관리자 권한을 요구하지 않는다. Windows Installer인 `msiexec`만 필요 시 승격한다(D2).
- 기존 WinFsp 교체/복구는 공용 드라이버 영향과 재부팅 가능성을 알리고 사용자 확인 후 진행한다(D3).
- 현재 전체 `ready` 의미를 유지하고 `core_ready` 및 기능별 준비 상태를 추가한다(D4).
- CI가 후보를 생성하고, 수동 Windows VM 검증 후 동일 배포물을 공개한다(D5).
- 독립 에이전트 검토는 사용자 선택으로 생략했다(D6). gstack 도구 로그에 등록된 리뷰로 주장하지 않는다.

비목표: 자동 업데이트, 별도 상주 설치 서비스, WinFsp 대체 드라이버, 전용 VM 자동화 인프라,
앱 코드 서명 인증서 도입, ARM64 배포, 사용자 데이터 이전/삭제. 시스템 드라이버 서명 검증은 필수다.

## 2. 기존 구조와 재사용 지점

Python 3.12 / PySide6 / SQLite 및 `presentation → application → domain` 경계를 유지한다.

| 기존 구성 | 사용 방식 |
|---|---|
| `scripts/build-package.ps1` | 앱 폴더를 한 번 생성하고 ZIP/설치형이 재사용. 기존 반환 ZIP 경로 계약 유지 |
| rclone 준비 스크립트와 vendor 명세 | 고정 버전/체크섬 준비 방식 확장. CI에서도 누락 없이 실행 |
| WinFsp Windows 진단 | DLL 존재 확인을 버전/실행 가능 여부를 포함하는 공통 상태 확인으로 확장 |
| RuntimeDiagnostics/doctor | 기존 필드 유지, 기본 앱 및 기능별 상태 추가 |
| GUI task/auth/error 패턴 | 검증과 설치 대기를 worker에서 실행하고 사용자 메시지를 공통 매핑 |
| 기존 사용자 데이터 경로와 종료 처리 | 프로필/DPAPI/로그/캐시 보존, 연결 중 교체 및 제거 보호 |
| 기존 check/package/release 작업 | 설치형 검사 및 수동 검증 후 공개 게이트를 추가 |

SSOT 검색 근거: graft 질의 `installer portable ZIP WinFsp runtime release packaging execution plan`,
rg의 `build-package`, `winfsp_available`, `RuntimeDiagnostics`, `doctorPayload.status`,
`build_application_services`, `release.yml`. 기존 설치형과 WinFsp 설치 서비스는 확인되지 않았다.

참조 권위: [아키텍처](../../../ARCHITECTURE.md), [계획 규약](../../PLANS.md),
[디자인](../../DESIGN.md), [프런트엔드](../../FRONTEND.md),
[보안](../../SECURITY.md), [신뢰성](../../RELIABILITY.md),
[배포 안내](../../../packaging/README.md), [기존 S3 계획](003-s3-drives.md),
[기술 부채](../tech-debt-tracker.md).

## 3. 구현 계약과 설치 흐름

### 공통 런타임 입력과 배포물

- 초기 고정 입력은 rclone 1.75.1과 WinFsp 2.1.25156. 빌드 시 공식 출처·버전·SHA-256·
  WinFsp 서명자·필수 MSI 기능·라이선스/고지를 검증한 명세를 사용한다.
- 실제 파일, 라이선스/고지, 명세 중 하나라도 누락되거나 검증 실패하면 배포 빌드를 중단한다.
  파일을 실행 시점에 최신 버전으로 교체하지 않는다.
- WinFsp는 실행 필수 구성 요소인 F.Main/F.User를 설치하고 SDK는 제외한다.
- 동봉 MSI, 명세, 고지를 두 형식에서 같은 상대 경로로 보관한다. ZIP 전체 파일 manifest에도 포함한다.
- 설치 도구는 Inno Setup으로 선택한다. 기존 폴더형 빌드 재사용과 설치 위치/바로가기/제거 지원이 이유다.
  WiX는 초기 구성 부담 때문에 채택하지 않는다. 도구 버전은 검증 후 빌드 설정에 고정하고
  라이선스 및 상업적 사용 안내를 배포 문서에 기록한다.
- 별도 설치형 빌드 스크립트는 준비된 공통 앱 폴더와 버전을 받아 Setup.exe와 SHA-256을 만든다.
  TEST-ONLY 입력은 설치형에서도 표시를 유지하며 정식 공개 대상에 포함하지 않는다.

### 서비스와 권한 경계

- 하나의 typed Application 서비스가 상태 확인/설치/결과 재확인을 담당한다. 설치 규칙·버전 판정·
  오류 코드를 Inno Setup, GUI, CLI에 각각 구현하지 않는다.
- Infrastructure는 Windows 진단, MSI 검증, 프로세스 관측, 설치 실행 포트를 구현한다.
  생산 어댑터 조합은 기존 bootstrap에 두되 사용자 DB/AWS를 열지 않는 별도 조합 함수를 사용한다.
- CLI에 `runtime winfsp inspect --output json`과 `runtime winfsp install --output json`을 추가한다.
  일반 앱 초기화 및 execution logging 이전에 분기한다. 임의 MSI 경로/URL/실행 명령 인자는 받지 않는다.
- GUI는 CLI 출력을 파싱하지 않고 동일한 Application 서비스를 직접 호출한다.
- 설치형도 공통 설치 CLI를 호출한다. 이미 승격된 설치형에서는 msiexec를 직접 실행하고,
  일반 권한 GUI/CLI에서는 ShellExecuteEx의 runas로 msiexec만 승격해 프로세스 종료 코드를 확인한다.
- 사용자 지정 경로는 안전한 인자 구성으로 전달한다. 쉘 명령 문자열로 조합하지 않는다.
  MSI 해시/서명 검증과 실행 사이에 파일이 교체되지 않도록 읽기 핸들로 쓰기/삭제를 막고
  동일한 정규화 경로의 파일을 실행한다. 잠금을 확보하지 못하면 설치를 시작하지 않는다.
- 설치 전후 AWS 인증, 프로필 DB, DPAPI 설정, 다른 관리자 계정의 사용자 데이터 초기화는 수행하지 않는다.

```text
GUI / runtime CLI / Setup
  → 공통 상태 확인
  → 신규 설치 또는 확인된 교체/복구
  → 동봉 MSI 검증 및 파일 고정
  → msiexec [필요할 때만 UAC, /norestart]
  → 종료 코드 확인
  → 원래 호출자 권한으로 재진단
  → 상태/사용자 메시지 반환
```

### 상태와 결과

- 진단 DTO에는 설치 버전과 상태를 추가한다: ready, missing, repair_required,
  unsupported, reboot_required. 설치 중/대기 중은 작업 DTO/화면 상태로 표현한다.
- 기존 `winfsp_available`, 전체 `ready`, doctor의 기존 필드/종료 코드 계약을 유지한다.
  `core_ready`는 DB/DPAPI/로그/Plugin/helper 등 기존 기본 앱 조건을 표현하고,
  기능별 `s3_mount.ready`와 사유는 WinFsp/rclone 조건을 포함한다.
- 준비된 WinFsp 2.x 중 동봉 버전 이상은 재사용한다. 낮은 버전/손상은 사용자 확인 후
  공식 MSI의 업그레이드/유지보수 경로로 처리한다. 알 수 없는 주요 버전은 unsupported로 표시하고
  자동 다운그레이드하지 않는다. 수동 registry 삭제/드라이버 파일 덮어쓰기는 하지 않는다.
- 설치 결과는 installed, cancelled, busy, reboot_required, failed, pending으로 구분하고
  원본 MSI 코드는 기술 진단에 유지한다. 0은 재진단, 3010/1641은 재부팅 필요,
  1602/UAC 1223은 취소, 1618은 다른 설치 진행 중, 나머지는 명확한 실패 메시지로 매핑한다.
- /norestart를 사용하며 자동 재부팅하지 않는다. 재부팅 필요 상태는 OS 재부팅 전 연결을 막고
  일반 호출자는 사용자 상태 파일, 승격된 Setup은 HKLM 상태에 보존한다. OS BootId와 비교해 재부팅 후 재진단한다.
  DB migration은 추가하지 않는다.
- 한 번에 한 설치만 시작한다. 중복 클릭/다른 앱 인스턴스는 busy로 처리하고,
  설치 중 진행률이 없으면 단계와 경과 시간만 표시한다.
- 15분 대기 후에도 끝나지 않으면 pending으로 안내한다. msiexec를 강제 종료하거나
  자동으로 두 번째 설치를 시작하지 않는다. 같은 실행 중에는 원래 작업을 계속 관측한다.
  앱을 다시 실행했을 때는 실제 설치/재부팅 상태를 확인한 뒤 재시도 여부를 판단한다.
- 사용 중인 우리 앱의 마운트는 교체/복구를 막고 해제를 안내한다. 외부 프로그램 사용을
  확실히 확인하지 못한 경우 이를 안내하고 닫도록 요청한다. 강제 종료하지 않는다.

### GUI 및 설치 완료

- S3 마운트/환경 진단은 공통 상태 DTO를 사용한다. 미설치 시 WinFsp 설치,
  복구 필요 시 설명과 별도 확인 후 설치/복구 버튼을 제공한다.
- 앱 시작만으로 설치/UAC를 실행하지 않는다. 설치 중에는 설치와 마운트 연결 액션을 막으며
  다른 기능은 사용할 수 있다. 검증·설치 대기는 worker에서 수행한다.
- 취소/실패 후 원래 화면으로 돌아오며 입력값을 유지하고 재시도를 제공한다.
  MSI 누락·해시·서명 실패는 검증 실패로 표시하며 실행하지 않는다.
- 설치형은 WinFsp 설치가 취소/실패해도 완료한다. 완료 화면에서 앱 설치와 S3 준비 상태를 구분한다.
- 설치형 완료 후 GUI를 관리자 권한으로 자동 실행하지 않는다. 일반 사용자 바로가기를 제공한다.

## 4. 업데이트·제거·런타임 영향

- 설치형의 고정 AppId/제거 등록을 사용하고 업그레이드는 기존 설치 위치를 재사용한다.
  데이터는 기존 사용자별 LOCALAPPDATA에 유지하여 ZIP/설치형 간 설정을 공유한다.
- 설치·업데이트·제거 전에 대상 폴더의 GUI/CLI/helper 및 소유 프로세스 사용을 확인한다.
  실행 중이면 트레이의 완전 종료 또는 외부 EC2 터미널 종료를 안내하고 재시도한다.
  자동 강제 종료·무조건적인 Restart Manager 종료·활성 폴더 덮어쓰기는 사용하지 않는다.
- 저장 설정, 프로필, DPAPI entropy, 로그, 미전송/복구 VFS 캐시는 업데이트/제거로 삭제하지 않는다.
  현재 S3 종료 정책은 연결 소유권 정리 후 복구 캐시를 보존한 채 종료할 수 있다는 점을 유지한다.
- 공용 WinFsp는 앱 제거 시 제거하지 않는다. 제거는 앱이 설치한 파일/바로가기/제거 등록만 대상으로 한다.
- ZIP은 새 폴더에 압축 해제하여 수동 교체한다. 기존 설치 위치/실행 중인 폴더 덮어쓰기를 안내하지 않는다.
- 부수효과: 빌드 네트워크 다운로드/파일 크기 증가, 설치 시 관리자 권한·드라이버 변경·
  재부팅 필요 가능성, 바로가기/제거 등록, 로컬 상태 파일 추가. 새 네트워크 포트나 상주 서비스는 추가하지 않는다.

## 5. 작업 순서와 수용 기준

| 순서 | 작업 | 완료 기준 |
|---|---|---|
| 1 | 런타임 명세/DTO/오류/진단 계약 확정 및 권위 문서 갱신 | 기존 필드 호환성과 일반 권한 설치 경계가 테스트로 고정됨 |
| 2 | 상태 확인·검증·설치 Application/adapter 및 CLI | 모의 오류/취소/재부팅/중복 설치, DB/AWS 초기화 없음 검증 |
| 3A | GUI 설치/복구 연결 | 기존 UI 간격/작업 runner 재사용, 입력 유지/재시도/연결 제한 검증 |
| 3B | 공통 payload/ZIP/Setup 구성 | 같은 앱 버전/파일/런타임, 오프라인 구성, 업그레이드/제거 보호 |
| 4 | 패키지 검사 및 CI 후보 생성/공개 분리 | 두 산출물과 SHA-256 생성, TEST-ONLY 공개 차단 |
| 5 | 수동 VM 검증과 공개 게이트 | 아래 실환경 증거가 후보 파일의 SHA-256과 일치 |

3A와 3B는 1~2의 공통 계약 이후 별도 작업이 가능하다. 공통 빌드/명세 수정은 한 작업이 소유하고,
통합 검증과 workflow 변경은 두 작업 병합 후 수행한다. 구현 시 동시에 같은 파일을 수정하지 않는다.

수용 기준:
- [ ] Setup/ZIP의 앱 파일·런타임 버전·해시 일치. 동봉 파일 누락/변조/서명 실패 시 빌드 또는 설치 차단.
- [ ] 일반 사용자/다른 관리자 계정 승인에서도 원래 사용자 데이터 유지, 설치 경로에서 DB/AWS 초기화 없음.
- [ ] WinFsp가 없는 정상 ZIP의 core_ready와 S3 미준비 상태를 구분하고 기본 기능 사용 가능.
- [ ] 신규/교체/복구/취소/1618/실패/재부팅/대기 초과/중복 클릭 상태에서 명확한 안내 및 재검사 가능.
- [ ] 연결 중 업데이트/제거 차단, 설정/미전송 캐시/공용 WinFsp 보존.
- [x] 기존 CLI·GUI·로컬 패키지 검사, 타입/경계/보안/문서 게이트 통과.
- [ ] 깨끗한 Windows 10/11 x64에서 설치/업데이트/제거/ZIP 시나리오 증거 확보.
- [ ] 검증된 동일 배포물만 승인 후 공개. 이 체크 완료 전 정식 배포 완료로 표시하지 않음.

## 6. 검증 계획과 실패 시나리오

| 경로 | 현실적인 실패 | 필수 검증 및 사용자 결과 |
|---|---|---|
| payload 준비 | 누락/변조/라이선스 누락 | 빌드·설치 검증 실패, 설치 미실행 |
| 상태 확인 | registry/DLL만 남은 불완전 설치 | 버전/준비 검사, repair_required 표시 |
| UAC/설치 | 취소/다른 관리자 계정/1618 | 원래 앱/데이터 유지, 취소·busy와 재시도 |
| MSI 결과 | 실패/3010/1641/15분 초과 | 명확한 실패·재부팅·pending 표시, 강제 종료/자동 중복 없음 |
| GUI 조작 | 중복 클릭/이동/종료 | 단일 작업·안전한 종료·다시 상태 확인, 입력 유지 |
| 교체/복구 | 우리 앱/외부 프로그램이 드라이버 사용 | 해제/닫기 안내 및 별도 확인, 강제 종료 없음 |
| 업데이트/제거 | 실행 중 파일/미전송 캐시 | 설치 경로 사용 보호, 설정·캐시·WinFsp 불변 |
| 공개 | 검증 후 파일 변경/검증 기록 누락 | hash 불일치/증거 누락 시 공개 차단 |

```text
자동: manifest/버전/서명 → 공통 서비스/오류 → CLI 초기화 경계 → GUI 상태 → ZIP/Setup 일치
실환경: 깨끗한 VM → 설치/취소/다른 계정 UAC → 재부팅 → 마운트 조회/해제
        → 업데이트/제거 → 데이터/캐시/WinFsp 보존 → 파일 해시 기록
```

자동 테스트는 pytest/Qt와 기존 PowerShell 패키지 검사에 추가한다.
Application fakes로 설치 정책을 검증하고 Windows adapter의 UAC/MSI/파일 잠금은 실환경 검증을 병행한다.
검증 도구가 없는 경로는 명확히 미검증으로 기록하며 모의 테스트를 실환경 증거로 대체하지 않는다.

실행 명령(구현 단계에서 실제 결과를 기록):

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/application tests/unit/infrastructure tests/adapter/cli tests/adapter/gui --no-cov -q --basetemp=.test-integrated-installer -o cache_dir=.test-cache-integrated-installer
.venv/Scripts/python.exe -m ruff check src tests tools
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe tools/architecture_check.py
./scripts/check.ps1
./scripts/setup-drive-runtime.ps1
./scripts/setup-winfsp-runtime.ps1
./scripts/build-package.ps1 -VendorDirectory ./vendor/session-manager-plugin
./scripts/build-installer.ps1 -PackageDirectory ./build/package/aws-connect
./scripts/test-package.ps1 -ZipPath <zip> -ExpectedWinFspState missing
./scripts/test-installer.ps1 -SetupPath <setup> -ExpectedWinFspState ready
```

위 setup-winfsp-runtime/build-installer/test-installer는 추가할 스크립트다. 입력 준비는
공통 vendor 명세를 사용해 검증된 MSI/고지를 준비한다. 설치형 빌드는 PackageDirectory의
검증된 공통 폴더와 VERSION.txt를 사용하고 선택적 OutputDirectory에 Setup/체크섬을 반환한다.
검사는 지정된 SetupPath/예상 WinFsp 상태를 사용하며 성공 시 exit 0, 미충족 시 nonzero와
명확한 증거를 반환한다. 실제 설치/재부팅 검사는 승인된 테스트 VM에서만 수행한다.
기존 test-package에는 ExpectedWinFspState 인자를 추가하여 missing/core_ready와 ready를 각각 확인한다.
아직 존재하지 않는 스크립트나 테스트를 실행한 것으로 기록하지 않는다.

VM 검증: Windows 10/11 x64, WinFsp 없음/설치됨/구버전, C/D·한글/공백 경로,
오프라인 설치, 읽기 전용 ZIP 실행 폴더, 취소/다른 관리자 계정/재부팅, 연결 중 업데이트,
제거 후 프로필/캐시 보존. AWS는 기존 승인 범위인 조회·연결·해제만 사용하며 쓰기/삭제는 제외한다.
테스트 DB에는 실제 자격 증명/리소스 ID를 넣지 않는다.

## 7. CI 및 공개 게이트

- main과 태그는 고정 입력 준비 → 전체 검사 → 공통 앱 폴더 → ZIP/Setup → 자동 패키지 검사 →
  두 배포물/체크섬/검증 metadata를 후보 artifact로 저장한다.
- 태그 푸시 즉시 공개하는 기존 단계는 수동 검증 후 공개 단계로 변경한다.
  후보는 태그/commit/실행 ID와 두 파일의 SHA-256으로 식별한다.
- VM 검증 기록에는 OS 빌드/원래 WinFsp 상태/검증 항목별 결과/두 후보 hash를 기록한다.
  하나라도 미통과·증거 누락·hash 불일치이면 공개하지 않는다.
- 공개 workflow는 수동 dispatch와 GitHub environment 승인으로 보호한다. 후보를 재빌드하지 않고
  검증한 동일 artifact를 받아 checksum과 증거를 확인한 뒤 ZIP/Setup 및 각각의 SHA-256을 공개한다.
- main artifact는 릴리스 후보이며 정식 공개 완료로 표현하지 않는다.
- 공급망 기존 PKG-001, clean VM PKG-002, CI PKG-003, 문서 검사 QA-001은 실제 상태를 재확인하고
  필요한 게이트를 해결한다. 검사를 통째로 제외하여 통과시키지 않는다.

## 8. 진행 기록·다음 단계

- 2026-10-13: 현행 Architecture/PLANS/active 003·004/부채·배포 코드·CI를 확인하고 본 계획 작성.
- 기존 작업 트리에 다른 작업의 많은 변경이 있다. 이번 작업은 본 계획 문서만 추가한다.
- 기능 구현, 설치형 빌드, 실제 WinFsp 변경, AWS 호출, 원격 공개는 수행하지 않았다.
- 문서 검사: `.venv/Scripts/python.exe -`에서 `local_markdown_links`로 본 문서의 로컬 링크
  9개 존재, UTF-8 읽기, 마지막 리뷰 보고서 위치를 확인 → passed.
- `./scripts/check.ps1 *> .qa-s3-drives/check-installer-plan.log` → documentation failed (exit 1).
  기존 vendor/graft 링크 문제인 QA-001이며 본 문서 링크 검사는 통과했다. 전체 게이트 통과는 아님.
- 다음 단계: 1의 계약·권위 문서부터 구현하고 가장 좁은 fake 기반 검사부터 수행한다.
- 수용 기준과 검증 증거가 모두 충족될 때만 completed로 이동한다.

### 2026-10-13 구현 및 로컬 후보 완료

- 위 초기 기록은 계획 작성 시점의 상태다. 사용자가 VM 없음으로 답변하여 이번 완료 범위를
  기능 구현, 로컬 배포 후보 및 검증 안내로 확정했다. 실환경/공개 게이트는 대체하지 않는다.
- 재사용 검색: `WinFspRuntimeService`, `core_ready`, `RUNTIME_SPEC`으로 공통 서비스,
  기존 진단 DTO와 단일 payload 명세의 참조를 확인했다. CLI/GUI/Setup은 같은 서비스 사용.
- Domain/Application 계약, bootstrap 조합, 독립 runtime CLI, Windows adapter,
  S3/환경 진단 설치·복구 UI, 공유 폴더 기반 ZIP/Setup 및 CI 후보/공개 분리를 구현했다.
- MSI 명세는 어댑터의 컴파일된 `RUNTIME_SPEC`에 한 번 정의한다. vendor manifest는 파생 출력이다.
  WinFsp 검사에서 DLL 버전뿐 아니라 filesystem driver 등록과 실제 파일도 확인한다.
- MSI 해시/서명 검증부터 종료까지 쓰기·삭제 방지 핸들을 보유하고 전역 mutex로 중복 실행을 막는다.
  15분 경과 시 GUI는 pending을 안내하며 원래 작업을 계속 관측한다. CLI는 최종 결과를 기다린다.
  잠금 조기 해제를 피하기 위한 구체화이며 설치 중 완전 종료는 완료 시점까지 보류한다.
- Setup: PC 전체 설치, 경로 선택/바로가기/선택적 WinFsp; 앱 프로세스 사용 중 업데이트·제거 차단.
  기존 공용 WinFsp 복구는 앱에서 별도 확인 후 수행한다. 사용자 데이터와 공용 드라이버는 보존한다.
- urllib3 감사에서 발견한 취약점 때문에 lockfile을 2.8.0으로 갱신했다. 추가 앱 의존성은 없다.
- QA-001은 프로젝트 문서 범위 검사와 회귀 테스트, 기존 줄바꿈 정규화로 해결했다.

| 실행 명령 | 실제 결과/증거 |
|---|---|
| `./scripts/check.ps1` | 전체 통과; 693 passed, 1 skipped, critical branch 100%, Ruff/mypy/architecture/docs/secrets/Bandit/audit 통과; `.qa-s3-drives/check-final-candidate.log` |
| `./scripts/build-package.ps1 -VendorDirectory ./vendor/session-manager-plugin` | production 입력으로 ZIP 생성; `.qa-s3-drives/build-final-candidate.log` |
| `./scripts/build-installer.ps1 -PackageDirectory ./build/package/aws-connect` | 공통 폴더의 manifest/hash 확인 후 Setup 컴파일 성공; `.qa-s3-drives/setup-final-candidate.log` |
| `./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-1.0.1-windows-x64.zip -ExpectedWinFspState ready` | exit 0; PATH 격리, 한글/공백/read-only 폴더, 별도 LOCALAPPDATA, CLI/GUI/helper/비밀 검사; `.qa-s3-drives/package-final-smoke.log` |
| `./scripts/test-installer.ps1 -SetupPath ./dist/aws-connect-1.0.1-windows-x64-Setup.exe` | exit 0, SHA-256 무결성만 확인; 실제 설치는 미실행; `.qa-s3-drives/installer-final-integrity.log` |
| `./build/package/aws-connect/aws_connect_cli.exe runtime winfsp inspect --output json` | 현재 PC의 ready / 2.1.25156 반환; 새 설치 없음 |
| `graft build` | 183 files / 2522 nodes 갱신; `.qa-s3-drives/graft-final.log` |

최종 로컬 후보(1.0.1):

| 파일 | 바이트 수 | SHA-256 |
|---|---|---|
| `dist/aws-connect-1.0.1-windows-x64.zip` | 116525847 | `7fb75d465fef9af0fcbf9636b0f7befaefbe785e666083cc0a347ad1fdca24e7` |
| `dist/aws-connect-1.0.1-windows-x64-Setup.exe` | 78970389 | `68eea557e32b5662f9cefadac55f98da2670fc8fcca00e5081d7caa39070bb9f` |

- 설치 안내/VM 필수 항목/증거 JSON/공개 절차: [packaging README](../../../packaging/README.md).
- 미검증: 깨끗한 Win10/11, WinFsp 없는 실환경, 다른 관리자 UAC, 취소/재부팅/실제 제거·보존,
  원격 CI와 environment 승인. 해당 상태의 정책은 모의 테스트로만 검증했다.
- 사이드이펙트: 배포 크기 증가, 런타임 설치 시 UAC/시스템 드라이버/재부팅 상태 기록.
  이번 작업에서는 compiler만 workspace에 설치했고 WinFsp 변경 설치·AWS 작업·원격 공개는 없다.
- 다음 단계: 검증 담당자가 VM 체크리스트를 실제 수행하고 동일 후보 hash 증거를 작성한다.
  GitHub `release` environment required reviewers 설정 및 원격 workflow 검증 후 공개한다.
  모든 외부 수용 기준까지 충족할 때 본 계획을 completed로 옮긴다.
- 문서 최종 갱신 후 `UV_CACHE_DIR=.uv-cache; ./scripts/verify-docs.ps1` → passed.
  최초 기본 cache 실행은 workspace 밖 uv cache 접근 거부로 실패하여 workspace cache로 재실행했다.
  존재하지 않는 `tools/secrets_scan.py` 호출은 실패했으며 실제 도구인 detect-secrets로 교정했다.
- `detect-secrets scan`을 `rg --files src tests tools scripts packaging .github docs ARCHITECTURE.md`의
  tracked/new 파일 모두에 실행 → potential secrets 0. 공식 Inno SHA-256 한 건은 공개 checksum임을
  확인하고 해당 줄만 allowlist 주석 처리했다. 런타임/배포 바이트 변경은 없다.

## GSTACK REVIEW REPORT

### v1.0.2 commit/push preparation — 2026-10-13

- 사용자 요청 메시지: 제목 `v1.0.2`, 본문 `- setup 수정`. 현재 main에 커밋·push하며 태그는 생성하지 않는다.
- SSOT 검색: `project.version`, `VERSION`, `1.0.1` 확인. 실제 버전 원본은 pyproject.toml이다.
  프로젝트의 3자리 버전 규칙을 유지해 1.0.2로 갱신하고 `uv lock --offline`으로 lock을 동기화했다.
- 실행 중인 개발 GUI가 `.venv/Scripts/aws-connect.exe`를 사용하여 기존 환경 uv sync가 실패했다.
  앱을 중단하지 않고 `UV_PROJECT_ENVIRONMENT=.venv-ship-1.0.2`, `UV_CACHE_DIR=.uv-cache`로 격리했다.
  온라인 `uv sync --locked` 성공. 초기 offline sync는 hatchling cache 부재로 실패했다.
- schema 생성은 `./scripts/generate-docs.ps1`로 재실행해 성공했다. 초기 잘못된 GUI entrypoint
  호출은 ACL 검사에서 실패했으며 AWS 요청/연결 또는 비밀 출력은 없었다.
- `./scripts/check.ps1` → exit 0, 693 passed/1 skipped, 타입/문서/포맷/경계/보안/audit 모두 통과.
  증거 `.qa-s3-drives/check-v1.0.2-ship.log`.
- tracked/new source·테스트·도구·배포·문서 대상 detect-secrets → 0; `git diff --cached --check` 통과.
- ship 스킬의 로컬 checklist가 누락되어 공식 upstream `review/checklist.md`를 읽고
  설치 권한/명령 인자/파일 잠금/상태 소비자/문서 검사/CI 공개 게이트를 재검토했다. 새 차단 결함 없음.
  기존 계획의 독립 검토 생략 결정 유지. 신규 PR/태그/정식 Release 생성은 요청 범위에서 제외한다.
- 1.0.2 ZIP 생성 성공: `.qa-s3-drives/build-v1.0.2-ship.log`.
  `dist/aws-connect-1.0.2-windows-x64.zip` SHA-256
  `344f4209ce483ac50c5d95fd354512fb85aa9931eeb3d4eea30936e616db0628`.
- 스테이징의 `.mount-toggle-secrets.json`과 별도 004 계획, 기존 로컬 검사 파일/삭제는 제외했다.
  기존 main 로컬 커밋 2개는 정상 fast-forward push에 함께 포함된다. 강제 push하지 않는다.
- `./scripts/test-package.ps1 -ZipPath ./dist/aws-connect-1.0.2-windows-x64.zip -ExpectedWinFspState ready`
  → exit 0; `.qa-s3-drives/package-v1.0.2-ship.log`.
- `./scripts/build-installer.ps1 -PackageDirectory ./build/package/aws-connect` → exit 0,
  Setup 1.0.2 컴파일 성공; `.qa-s3-drives/setup-v1.0.2-ship.log`.
- `./scripts/test-installer.ps1 -SetupPath ./dist/aws-connect-1.0.2-windows-x64-Setup.exe` → exit 0,
  무결성만 검증; `.qa-s3-drives/installer-v1.0.2-ship.log`. 실제 설치는 미실행.
- 최종 `./scripts/verify-docs.ps1` → exit 0. 로컬 배포 후보와 VM/공개 미검증 구분을 유지한다.
- 다음 실행: 위 지정 메시지로 커밋한 뒤 origin/main에 정상 push하고 원격 HEAD 일치를 확인한다.

| Review | 상태 | 결정 및 결과 |
|---|---|---|
| Eng / plan-eng-review | 대화형 검토 완료 | 범위 유지; 권한/공용 드라이버/진단 호환성/VM 공개 게이트 보완 확정 |
| Code quality | 추가 범위 변경 없음 | 공통 서비스/명세/오류 처리 재사용 |
| Test review | 검증 계획 확정 | 모의 검증과 UAC/설치/재부팅/데이터 보존의 실환경 검증 분리 |
| Performance | 구현 조건 확정 | worker·분할 해시·관측 유지, 상주 서비스/주기적 MSI 검증 없음 |
| Outside voice | 생략 | 사용자 D6 선택 |

- UNRESOLVED: 사용자 선택 사항 없음. 구현·실환경·기존 배포 게이트는 미완료.
- VERDICT: 리뷰 결정 반영 완료, 구현 착수 가능. 출시 준비 완료 또는 테스트 통과 판정은 아님.
- 도구 기록: gstack 보조 도구 로그/대시보드 등록 미실행. 본 표는 대화에서 수행한 검토 기록이다.
