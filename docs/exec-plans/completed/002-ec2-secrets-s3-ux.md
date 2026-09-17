# EC2·Secrets·S3 운영 UX 개선 실행 계획

## 상태

- 작성일: 2026-09-15
- 상태: 완료
- 완료 시 이동: `docs/exec-plans/completed/002-ec2-secrets-s3-ux.md`
- 선행 조건: 현재 작업 트리의 기존 변경을 보존하고 관련 파일만 수정한다.

## 사용자 결과

- EC2 표는 전체 폭을 사용하고 Name 열이 남는 공간을 모두 차지한다.
- 저장된 Secret은 조회 당시 값과 조회 컨텍스트를 SQLite에 보관하며 로컬에서 수정할 수 있다.
- S3 업로드 전 선택한 파일·폴더를 목록으로 확인하고 충돌 처리 방식을 선택할 수 있다.
- S3 객체 목록에서 파일과 폴더를 함께 다중 선택하여 다운로드할 수 있다.

## 비목표

- 저장 Secret 수정 내용을 AWS Secrets Manager에 반영하지 않는다.
- Secret 저장 시 DPAPI 암호화를 추가하지 않는다.
- S3 전송 동시성을 늘리거나 별도 프로세스를 시작하지 않는다.
- EC2/RDS/인증의 기존 Application 계약을 재작성하지 않는다.

## 가정과 결정

- “왼쪽 저장리스트에서 클릭한 요소를 수정”은 선택한 동일 SQLite 행의 Secret ID와 Value를
  수정한다는 의미다. 내부 row ID는 사용자 편집 대상이 아니다.
- 저장 항목 선택 시 조회 방식, Secret ID와 중계 EC2를 자동 선택하고 저장된 Value를 표시한다.
  선택만으로 AWS 재조회는 실행하지 않는다.
- S3 “폴더 지정”은 로컬 폴더 추가를 뜻한다. 하위 파일을 재귀 수집하고 선택 폴더 기준 상대 경로를
  현재 S3 Prefix 아래에 보존한다.
- 사용자 승인에 따라 Secret Value를 SQLite 평문으로 저장한다. 로그 마스킹과 UI의 제한적 노출은
  기존 규칙을 유지한다.

## SSOT 조사

검색어: `setSectionResizeMode|setColumnWidth|resizeEvent|SavedSecret|remember|update_saved_secret|`
`saved_secrets|_selected_files|dropEvent|prepare_upload|_confirm_upload|download_object`

- EC2 열 구성과 반응형 폭: `src/aws_connect/presentation/gui/ec2_rds.py`
- Secret 모델·유스케이스·저장 port: `domain/saved_secret.py`, `application/secrets_service.py`,
  `application/ports.py`
- SQLite migration과 CRUD: `infrastructure/sqlite_profile_store.py`
- S3 선택·표시·전송 orchestration: `presentation/gui/s3.py`
- S3 경로·preflight·전송 규칙: `application/s3_service.py`
- S3 AWS 호출: `infrastructure/aws_s3_gateway.py`

기존 계약을 확장하며 같은 규칙을 GUI에 중복 구현하지 않는다.

## 설계 대안

- **SavedSecret 모델 확장 — 추천:** 식별자, 값과 조회 컨텍스트의 동일 수명주기를 한 레코드에서
  관리한다. 별도 값 테이블은 조인과 동기화 책임만 늘어난다.
- **별도 SecretValue 테이블:** 향후 값 이력에는 유리하지만 현재 요구에는 과도한 추상화다.
- **S3 배치 Plan DTO — 추천:** 경로 검증, 충돌 정책과 대상 집합을 Application의 SSOT로 둔다.
- **GUI에서 선택별 즉시 전송:** 구현은 작지만 폴더 재귀, 중복 제거, 취소와 부분 실패가 분산된다.
- **AWS CLI `s3 sync` subprocess 직접 실행 — 비추천:** `--no-overwrite`의 동작은 요구사항과 맞지만
  AWS CLI 없는 Portable ZIP이라는 제품 계약을 위반한다. 동일 규칙을 기존 boto3 gateway로 구현한다.

## 구현 순서

### 1. 권위 문서 갱신

- `docs/product-specs/aws-connect.md`에 Secret 로컬 값 저장·수정과 S3 파일/폴더 배치 흐름을 추가한다.
- `docs/DESIGN.md`에 제거할 문구, 선택 목록, 동적 버튼과 다중 선택 상태를 확정한다.
- `docs/SECURITY.md`에 Secret 평문 SQLite 저장 예외와 잔여 위험을 명시한다.
- `docs/RELIABILITY.md`에 폴더 탐색, 배치 취소, 부분 성공과 원자 다운로드 규칙을 추가한다.

### 2. EC2 전체 폭 재배분

- 즐겨찾기, Instance ID, Private IP, EC2 상태와 작업 열은 고정 폭으로 유지한다.
- Instance ID와 EC2 상태를 현재보다 소폭 넓힌다.
- Name 열만 `QHeaderView.Stretch`로 지정하고 `resizeEvent`의 Name 수동 폭 변경을 제거한다.
- 1024×720과 1424×894에서 가로 스크롤과 작업 버튼 잘림을 검사한다.

### 3. Secret 로컬 저장·편집

- `SavedSecret`에 `value`, `lookup_mode`, nullable `relay_instance_id`를 추가한다.
- SQLite migration 8로 열을 추가하고 기존 행에는 빈 Value와 직접 조회 기본값을 적용한다.
- 조회 성공 시 Secret ID, Value, 조회 방식과 중계 EC2를 한 transaction으로 upsert한다.
- 수정은 선택된 row ID 기준으로 ID와 Value를 저장하며 AWS `PutSecretValue`는 호출하지 않는다.
- 저장 항목 선택 시 Secret selector/input, 조회 방식과 중계 EC2를 복원한다. 저장된 중계 EC2가
  현재 목록에 없으면 값을 유지하되 사용 불가 상태를 명확히 표시한다.
- 다음 문구를 제거한다.
  - `조회 가능한 Secret n개`
  - `SQLite 저장 항목 n개`
  - 우측 상단 `Secret 값 ...`
  - `경유 조회는 SSM Run Command ...` 장문 안내
- 등록·조회·수정·삭제 후 가능한 경우 동일한 저장 row 선택을 유지한다.

### 4. S3 업로드 선택 목록

- drop zone 클릭과 drag/drop으로 파일과 폴더를 누적 추가한다.
- 중복 로컬 경로를 제거하고 이름, 상대 경로와 크기를 간단한 목록에 표시한다.
- 항목 제거와 전체 비우기를 제공한다.
- 별도 `파일 선택` 버튼과 상단 `파일 추가` 버튼을 제거한다.
- 하단 단일 버튼은 대상이 있을 때 파란 `업로드`, 전송 중 빨간 `업로드 취소`로 전환한다.
- 폴더는 Application에서 재귀 탐색하며 링크 순환, 읽기 실패, `..`와 절대 S3 key를 거부한다.
- 기존 `overwrite: bool`을 명시적인 `OVERWRITE`/`SKIP_EXISTING` 충돌 정책으로 교체한다.
- `SKIP_EXISTING`은 AWS CLI의
  `aws s3 sync "{로컬 업로드 폴더}" "s3://{버킷}/{prefix}" --no-overwrite`와 동일하게,
  대상 key가 이미 존재하면 로컬 파일의 크기·수정 시간과 관계없이 건너뛰고 신규 key만 업로드한다.
  실제 AWS CLI 프로세스는 실행하지 않고 기존 boto3 gateway와 인증 세션을 재사용한다.
- `OVERWRITE`는 충돌한 기존 key를 포함한 선택 대상 전체를 업로드하고, `SKIP_EXISTING`은 preflight와
  각 전송 직전 재확인에서 존재하는 key를 제외한다. skip된 key는 완료 요약에 별도로 표시한다.
- 충돌 대화상자는 대표 파일명 1개와 `외 N건의 중복 파일이 있습니다.`만 표시하고
  `덮어쓰기`, `무시하기`, `취소`를 제공한다. 전체 key/파일명은 대화상자에 나열하지 않는다.

### 5. S3 다중 다운로드

- 객체 표를 다중 행 선택으로 변경하고 `선택 파일 열기`를 `다운로드`로 변경한다.
- 파일과 prefix 선택을 `DownloadPlan`으로 만들고 중첩 선택은 key 기준으로 중복 제거한다.
- prefix의 전체 하위 객체를 pagination 조회하며 폴더 marker는 다운로드 대상에서 제외한다.
- 사용자가 대상 루트 폴더를 한 번 선택하면 S3 상대 경로를 로컬에 보존한다.
- 경로 이탈, 로컬 대상 중복과 기존 파일 충돌을 전송 전에 검증한다.
- 기존 임시 파일 + atomic replace를 재사용하고 진행률, 취소와 부분 실패 요약을 제공한다.

## 수용 기준

- EC2 Name 열이 남는 폭 전체를 사용하고 Instance ID·EC2 상태 열은 더 넓어지며 작업 열은 잘리지 않는다.
- 지정된 Secret 건수/요약/경유 안내 문구가 화면에서 사라진다.
- 조회한 Secret의 ID, Value와 조회 EC2가 SQLite에 자동 저장되고 재선택·로컬 수정 후 재시작해도 유지된다.
- 저장 Secret 수정은 AWS API를 호출하지 않는다.
- 업로드 대상 파일과 폴더 내부 파일이 전송 전에 목록으로 보이고 개별 제거할 수 있다.
- 단일 버튼이 `업로드`와 `업로드 취소` 상태를 정확히 전환한다.
- 충돌 시 덮어쓰기는 기존 key까지 업로드하고, 무시하기는 `aws s3 sync --no-overwrite`와 동일하게
  기존 key를 전부 제외하며, 취소는 어떤 전송도 시작하지 않는다. 경고에는 대표 1건과 나머지 건수만 보인다.
- 연결된 Bucket에서 파일과 폴더를 혼합 다중 선택하여 상대 경로를 보존한 채 다운로드할 수 있다.

## 실패 모드와 처리

| 경로 | 실패 | 처리 |
|---|---|---|
| Secret migration | 기존 DB 또는 SQLite busy | transaction rollback과 명확한 저장 오류 |
| 저장 중계 EC2 복원 | 인스턴스 삭제/권한 없음 | 저장값 유지, 사용 불가 표시, 자동 AWS 호출 금지 |
| 폴더 업로드 | unreadable file/symlink loop | 해당 경로를 포함한 typed error, 전송 시작 금지 |
| 업로드 충돌 | 모두 기존 객체 | `SKIP_EXISTING` 결과와 건수를 명시하고 실제 전송 0건을 구분 |
| preflight 이후 충돌 | 업로드 직전 동일 key 생성 | `SKIP_EXISTING`은 재확인 후 제외, `OVERWRITE`만 교체 |
| 폴더 다운로드 | pagination/권한 실패 | 계획 생성 실패로 표시, 부분 전송 시작 금지 |
| 배치 다운로드 | 중간 네트워크 실패/취소 | 완료·실패·취소 항목 요약, 임시 파일 정리 |
| 로컬 경로 충돌 | 동일 상대 경로/기존 파일 | 전송 전 확인 또는 명시적 정책 적용 |

## 테스트와 검증

1. Domain/Application/GUI focused tests

```powershell
uv run --no-sync pytest tests/unit/domain/test_saved_secret.py tests/unit/application/test_secrets_service.py tests/unit/application/test_s3_service.py tests/adapter/gui/test_ec2_rds.py tests/adapter/gui/test_secrets.py tests/adapter/gui/test_s3.py -q --no-cov
```

2. SQLite/S3 adapter tests

```powershell
uv run --no-sync pytest tests/integration/infrastructure/test_sqlite_profile_store.py tests/integration/infrastructure/test_aws_s3_gateway.py -q --no-cov
```

3. 생성 문서와 전체 게이트

```powershell
./scripts/generate-docs.ps1
./scripts/verify-docs.ps1
./scripts/check.ps1
```

4. GUI 렌더 QA

```powershell
uv run --no-sync python tools/render_gui.py .visual-qa-002-1024 --width 1024 --height 720
uv run --no-sync python tools/render_gui.py .visual-qa-002-1424 --width 1424 --height 894
```

## 런타임·보안 사이드이펙트

- migration 8 이후 Secret 원문 Value가 SQLite에 평문 저장된다.
- 폴더 upload/download는 파일 수만큼 디스크 탐색과 S3 API 호출을 수행한다.
- 배치 작업은 기존 취소 가능한 Operation에서 직렬 실행해 네트워크 동시성을 늘리지 않는다.
- 프로세스 시작, 포트 사용과 애플리케이션 초기화 순서에는 영향이 없다.

## 진행 기록

- 2026-09-15 `rg --files`, `git status --short`, `ARCHITECTURE.md`, 기존 활성 계획과 관련 코드/테스트 조사 완료.
- 2026-09-15 위 SSOT 검색으로 기존 확장 지점 확인.
- 2026-09-15 EC2 구현과 Secrets/S3 변경 지점 조사를 cavecrew 서브에이전트 3개로 병렬 시작.
- 2026-09-15 EC2 열 재배분, Secret migration 8·로컬 복원/수정, S3 폴더 업로드·충돌 정책·
  다중 다운로드 구현 완료.
- 집중 검증: 계획 관련 Domain/Application/Infrastructure/GUI 124 passed, 1 skipped.
- 전체 `./scripts/check.ps1`: 문서 생성 정합성, Ruff format/lint, architecture/import boundaries,
  mypy, 406 passed·1 skipped(coverage 85.91%), critical coverage, secret scan, Bandit,
  dependency audit 모두 통과.
- `tools/render_gui.py`로 1024×720과 1424×894의 EC2/Secrets/S3 포함 전체 화면 렌더 완료.
- 독립 reviewer 서브에이전트 2개는 사용량 제한으로 실행되지 않았으며, 사용자가 자동 검증 결과를
  기준으로 완료 처리하도록 명시적으로 승인했다.

## 다음 단계

완료. 후속 실제 AWS smoke가 필요하면 별도 활성 실행 계획으로 시작한다.
