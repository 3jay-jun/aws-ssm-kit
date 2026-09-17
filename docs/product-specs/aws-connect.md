# aws-ssm-kit 제품 요구사항 문서

## 1. 문서 정보

- 제품명(가칭): **aws-ssm-kit**
- 대상 플랫폼: Windows 10/11 x64
- 배포 형태: Portable ZIP
- 문서 상태: 초안
- 기준 범위: 기존 BAT 기반 AWS 인증, EC2 SSM 접속, RDS 터널링 기능의 Python 애플리케이션 전환

## 2. 배경과 문제

현재 사용자는 AWS 접속을 위해 장기 Access Key/Secret Key 확인, MFA 입력, 임시 Session Token 발급 또는 재사용, EC2 선택, SSM 접속, RDS 포트포워딩을 여러 명령과 프로그램으로 수행한다.

기존 BAT가 이 과정을 일부 자동화하지만 다음 문제가 남아 있다.

- BAT와 INI를 직접 편집해야 한다.
- 장기 인증정보가 평문 파일에 저장될 수 있다.
- EC2 접속과 RDS 터널링 설정 관리가 불편하다.
- RDS 조회 권한이 없는 계정에서는 endpoint 자동 탐색을 사용할 수 없다.
- AWS CLI, gossm, Session Manager Plugin 등 여러 도구의 설치와 버전 관리가 필요하다.
- 오류 메시지와 로그가 사용자 관점에서 충분히 구조화되어 있지 않다.

## 3. 제품 목표

aws-ssm-kit는 다음 목표를 가진다.

1. AWS 인증과 MFA Session Token 관리를 한 애플리케이션에서 처리한다.
2. 온라인 SSM 관리 EC2 목록을 조회하고 선택하여 접속한다.
3. PuTTY의 Saved Session과 유사하게 RDS 터널 세션을 등록하고 재사용한다.
4. Secrets Manager와 S3의 자주 사용하는 작업을 제공한다.
5. AWS CLI, Python, gossm을 별도로 설치하지 않아도 실행할 수 있게 한다.
6. 민감한 인증정보를 SQLite에 평문으로 저장하지 않는다.
7. 실행에 필요한 오류와 권한 부족 원인을 사용자가 이해할 수 있게 안내한다.
8. 공통 및 기능별 비즈니스 로직을 CLI와 GUI에서 동일하게 재사용하고, 각 기능을 CLI로 독립 검증할 수 있게 한다.

## 4. 비목표

초기 버전에서는 다음을 목표로 하지 않는다.

- AWS 콘솔 전체 기능 대체
- IAM 사용자, 정책 또는 역할 생성·수정
- RDS 데이터베이스 클라이언트 기능
- S3 prefix 재귀 삭제 및 `sync --delete`
- Linux/macOS 지원
- 여러 사용자가 동시에 사용하는 중앙 서버형 서비스
- AWS 장기 인증정보를 다른 PC로 자동 이전
- Session Manager 프로토콜 자체 구현

## 5. 대상 사용자

- 개발·운영 환경의 EC2에 SSM으로 접속하는 사용자
- 사설망 RDS에 로컬 포트포워딩으로 접속하는 사용자
- Secrets Manager 값을 제한적으로 확인해야 하는 사용자
- S3에 파일을 업로드하거나 객체 목록을 확인하는 사용자
- AWS 명령어와 도구 설치 절차를 최소화하고 싶은 Windows 사용자

## 6. 배포 및 실행 조건

### 6.1 배포 구성

```text
aws-connect/
├─ aws_connect.exe
├─ aws_connect_cli.exe          # 기능 검증 및 진단용 CLI
├─ session-manager-plugin.exe
└─ licenses/
   ├─ session-manager-plugin-LICENSE
   ├─ session-manager-plugin-NOTICE
   └─ third-party-licenses.txt
```

실행 파일과 동봉 Plugin은 압축 해제 폴더에 두지만 변경 가능한 DB, DPAPI entropy와 로그는
최초 실행 시 `%LOCALAPPDATA%\AWSConnect` 아래에 생성한다. 따라서 앱 폴더는 읽기 전용이어도
정상 동작하고, 사용자 데이터는 Portable ZIP 교체 후에도 유지된다.

### 6.2 사용자 설치 요구사항

- Python 설치 불필요
- AWS CLI 설치 불필요
- gossm 설치 불필요
- Session Manager Plugin 별도 설치 불필요
- Portable ZIP 압축 해제 후 `aws_connect.exe` 실행
- 진단 또는 개별 기능 확인 시 `aws_connect_cli.exe` 실행

### 6.3 런타임 구성

- AWS API: boto3/botocore
- 로컬 데이터: SQLite
- 자격증명 암호화: Windows DPAPI
- EC2 대화형 세션 및 RDS 터널 데이터 채널: 동봉된 Session Manager Plugin

### 6.4 UI 기준

- UI 방식은 인증·관리 기능을 앱 내부에서 처리하고 EC2 대화형 셸만 외부 터미널로 여는 하이브리드 Windows GUI로 한다.
- 공식 UI 설계 기준은 [`docs/DESIGN.md`](../DESIGN.md)다.
- 시각적 기준 목업은 [`aws-connect-ui-mockup.html`](../design-docs/aws-connect-ui-mockup.html)이다.

### 6.5 CLI와 GUI 실행 인터페이스

- GUI는 일반 사용자를 위한 기본 실행 인터페이스다.
- CLI는 기능 개발, 자동화된 검증, 장애 진단과 GUI 연결 전 독립 테스트를 위한 실행 인터페이스다.
- CLI와 GUI는 인증, 프로필, EC2, RDS, Secrets Manager 및 S3 비즈니스 로직을 각각 구현하지 않고 동일한 Application Service를 호출해야 한다.
- CLI와 GUI의 차이는 입력 수집, 결과 표현, 사용자 상호작용에만 한정한다.
- 개별 기능은 Application Service와 CLI 검증을 먼저 완료한 후 GUI에 연결한다.

## 7. 핵심 사용자 흐름

### 7.1 최초 실행 및 AWS 프로필 등록

1. 사용자가 프로필 이름, Region, Account ID, IAM User ID, Access Key, Secret Key와 MFA 사용
   여부를 입력한다. 새 프로필의 MFA 사용 기본값은 활성화다.
2. 애플리케이션은 STS `GetCallerIdentity`로 자격증명을 검증한다.
3. 예상 Account ID와 IAM User가 실제 응답과 일치하는지 검사한다.
4. Access Key와 Secret Key는 DPAPI로 암호화하여 SQLite에 저장한다.
5. 검증 실패 시 잘못된 키, 비활성화된 키, Secret Key 불일치, 권한 부족을 구분해 안내한다.

### 7.2 임시 Session Token 관리

1. 선택한 프로필의 기존 임시 자격증명과 만료 시각을 확인한다.
2. 유효하고 만료까지 30분 초과로 남았다면 재사용한다.
3. 만료, 불일치, 파싱 오류 또는 만료 임박이면 프로필 설정을 확인한다. MFA 사용 프로필만
   6자리 코드를 요청하며, 미사용 프로필은 사용자 입력 없이 발급을 계속한다.
4. STS `GetSessionToken`으로 새 임시 자격증명을 발급한다. MFA 미사용 프로필은
   `SerialNumber`와 `TokenCode`를 요청에 포함하지 않는다.
5. 새 자격증명을 검증한 뒤 기존 값을 교체한다.
6. 임시 Access Key, Secret Key, Session Token도 DPAPI 암호문으로 저장한다.

### 7.3 EC2 접속

1. EC2 inventory와 SSM 관리 노드를 Instance ID로 합치되 목록에는 EC2 전원 상태만 표시하고,
   SSM 상태는 연결·재부팅 작업 가능 여부에 반영한다.
2. 사용자는 전체, 실행 중, 중지됨을 필터링하고 프로필·Region별 즐겨찾기를 지정한다.
3. EC2 조회 권한이 없으면 기존 SSM Online 정보만 표시한다.
4. 실행 중이며 SSM Online인 대상을 선택해 대화형 Session Manager 세션을 연다.
5. 실행 중이지만 SSM Offline이면 확인 후 재부팅하고, 중지됨이면 과금 안내 확인 후 시작한다.
6. 시작·재부팅의 비동기 상태와 세션 종료 결과를 사용자에게 표시한다.

### 7.4 RDS 터널 세션 관리

1. 등록된 터널 세션 목록을 표시한다.
2. 사용자는 새 세션을 등록하거나 기존 세션을 수정·복제·삭제할 수 있다.
3. 세션에는 RDS host, 원격 port, 로컬 port, 중계 EC2 선택 방식을 저장한다.
4. 중계 EC2는 고정 Instance ID 또는 실행 시 선택으로 설정한다.
5. 연결 전에 로컬 포트 사용 여부를 검사한다.
6. SSM `AWS-StartPortForwardingSessionToRemoteHost` 문서로 터널을 연다.
7. 연결 중에는 `localhost:로컬포트 → RDS host:원격포트`를 표시한다.
8. 애플리케이션 또는 세션을 종료하면 포트포워딩도 종료한다.
9. `DescribeDBInstances` 권한이 있으면 endpoint를 선택하고, 없으면 host를 직접 입력한다.

### 7.5 Secrets Manager 조회

1. 사용자가 Secret 이름 또는 ARN을 입력한다.
2. `GetSecretValue` 권한으로 SecretString을 조회한다.
3. JSON이면 키 목록과 값을 구조화하여 표시한다.
4. `password`, `secret`, `token`, `key` 성격의 필드는 기본 마스킹한다.
5. 사용자의 명시적 동작이 있을 때만 개별 값을 30초 동안 일시적으로 표시한다. GUI의 전체 내용 복사는 원문 전체를 클립보드에 복사하고 30초 뒤 같은 값이면 제거한다.
   AWS 키-값 수정 버튼은 제공하지 않는다.
6. Secret 목록 권한이 없을 수 있으므로 이름 직접 입력을 항상 지원한다.
7. 직접 조회 권한이 없으면 사용자가 명시적으로 Online EC2를 선택해 SSM Run Command로
   조회할 수 있다. 앱 계정은 Run Command 권한, 인스턴스 역할은 Secret 조회 권한이 필요하다.
8. `ssm:SendCommand` 권한이 없으면 사용자가 명시적으로 선택해 `ssm:StartSession` 기반 외부
   터미널에서 같은 고정 명령을 자동 실행할 수 있다. 실행 뒤 플랫폼 셸을 유지하며 결과는 앱이
   회수하거나 저장하지 않는다.
9. `ListSecrets` 권한이 있으면 상단 검색을 선택 상자로 제공하고, 없으면 이름/ARN 직접 입력으로
   전환한다.
10. 조회에 성공한 Secret 이름/ARN, 값, 조회 방식과 중계 EC2는 프로필별 SQLite 저장 목록에
    자동 등록한다. 왼쪽 저장 항목을 선택하면 마지막 조회 컨텍스트와 값을 복원하며 Secret ID와
    Value를 SQLite에서만 수정할 수 있다. 이 수정은 AWS Secret에 반영하지 않는다.

### 7.6 S3 조회 및 업로드

1. Bucket 목록 권한이 있으면 선택 상자를, 없으면 저장 위치 또는 직접 Bucket 입력을 사용한다.
2. 접근 가능한 객체 목록을 조회한다.
3. 클릭 또는 드래그 앤 드롭으로 로컬 파일과 폴더를 누적 선택하고 업로드 전 간단한 목록으로 확인한다.
4. 폴더의 상대 경로를 현재 Prefix 아래에 보존하고 대상 S3 URI와 충돌 건수를 사전에 확인한다.
5. 동일 key가 있으면 대표 파일 1개와 나머지 건수만 표시하고 덮어쓰기, 무시하기 또는 취소를 묻는다.
   무시하기는 `aws s3 sync --no-overwrite`와 동일하게 기존 key를 제외하고 신규 key만 전송한다.
6. 업로드 진행률과 완료 결과를 표시한다.
7. 대용량 파일은 multipart upload를 사용한다.
8. 선택한 단일 객체는 명시적 확인 후 삭제할 수 있으며 prefix 재귀 삭제는 제공하지 않는다.
9. 연결된 Bucket에서 파일과 폴더를 혼합 다중 선택해 지정한 로컬 폴더에 상대 경로를 보존하여
   다운로드할 수 있다. 각 파일은 임시 파일로 완전히 받은 뒤 원자적으로 교체한다.

## 8. 기능 요구사항

### 8.1 AWS 프로필

- FR-AUTH-001: 여러 AWS 프로필을 등록할 수 있어야 한다.
- FR-AUTH-002: 프로필별 Region, Account ID, IAM User ID를 관리해야 한다.
- FR-AUTH-003: Access Key와 Secret Key는 DPAPI로 암호화해야 한다.
- FR-AUTH-004: 프로필 저장 전에 STS 검증을 수행해야 한다.
- FR-AUTH-005: 기본 프로필을 지정할 수 있어야 한다.
- FR-AUTH-006: 프로필 삭제 시 관련 임시 세션을 함께 제거해야 한다.
- FR-AUTH-007: 새 프로필은 저장 전 임시 목록 항목으로 표시하고, 저장 성공 시 실제 레코드로
  전환해야 한다. 선택한 프로필은 새 고유 ID로 복제할 수 있어야 한다.
- FR-AUTH-008: 프로필별 MFA 사용 여부를 저장·수정할 수 있어야 하며 기존 프로필과 새 프로필의
  기본값은 활성화여야 한다.

### 8.2 MFA와 세션

- FR-SESSION-001: MFA 디바이스 ARN은 Account ID와 User ID에서 내부 생성하며 일반 사용자
  화면에 표시하지 않아야 한다. 기존 사용자 지정 ARN은 프로필 갱신 시 보존해야 한다.
- FR-SESSION-002: 임시 세션의 만료 시각을 저장해야 한다.
- FR-SESSION-003: 만료 또는 불일치 세션은 오류 종료 대신 재발급 흐름으로 전환해야 한다.
- FR-SESSION-004: 토큰 재발급은 한 작업당 자동 1회만 시도해야 한다.
- FR-SESSION-005: MFA 코드는 저장하거나 로그에 남기지 않아야 한다.
- FR-SESSION-006: MFA 미사용 프로필은 MFA 입력 상태를 만들지 않고 `GetSessionToken`의 MFA
  파라미터를 생략한 뒤 원래 작업을 한 번만 이어서 실행해야 한다.

### 8.3 EC2 SSM

- FR-EC2-001: EC2 전원 상태를 표시하고 SSM 연결 상태는 작업 버튼의 상태에 반영해야 한다.
- FR-EC2-002: 즐겨찾기, Name, Instance ID, IP, 플랫폼 정보를 제공해야 한다.
- FR-EC2-003: EC2 Describe 권한이 없어도 SSM 정보로 접속할 수 있어야 한다.
- FR-EC2-004: 선택한 EC2에 대화형 Session Manager 세션을 열어야 한다.
- FR-EC2-005: 즐겨찾기는 프로필과 Region별로 저장되고 목록 상단에 정렬되어야 한다.
- FR-EC2-006: SSM Offline 실행 인스턴스는 확인 후 재부팅하고 중지 인스턴스는 확인 후
  시작할 수 있어야 한다. Stop/Terminate 작업은 제공하지 않아야 한다.

### 8.4 RDS 터널 세션

- FR-RDS-001: 터널 세션의 등록·조회·수정·복제·삭제를 제공해야 한다.
- FR-RDS-002: 세션 이름은 중복될 수 없다.
- FR-RDS-003: host, remote port, local port를 필수 검증해야 한다.
- FR-RDS-004: 중계 EC2를 고정하거나 실행 시 선택할 수 있어야 한다.
- FR-RDS-005: 사용 중인 로컬 포트에서는 연결을 시작하지 않아야 한다.
- FR-RDS-006: RDS 조회 권한이 있으면 RDS instance endpoint 선택을 지원해야 한다.
- FR-RDS-007: RDS Describe 권한이 없어도 저장 세션으로 정상 동작해야 한다.

### 8.5 Secrets Manager

- FR-SECRET-001: Secret 이름 또는 ARN으로 값을 조회해야 한다.
- FR-SECRET-002: JSON과 일반 문자열 Secret을 모두 지원해야 한다.
- FR-SECRET-003: 민감 필드는 기본 마스킹해야 한다.
- FR-SECRET-004: Secret 원문은 로그에 저장하지 않아야 한다. 사용자가 승인한 저장 목록 기능은
  조회 Value를 현재 사용자 전용 SQLite에 평문으로 저장할 수 있다.
- FR-SECRET-005: 직접 조회 권한이 없을 때 명시적으로 선택한 Online EC2의 instance role로
  SSM Run Command 조회를 수행할 수 있어야 하며 자동 fallback으로 실행하지 않아야 한다.
- FR-SECRET-006: 중계 목록은 EC2 Name과 Instance ID를 함께 표시해야 한다. SendCommand가
  거부되면 명시적 StartSession 터미널 실행을 제공하고, 이 경로도 실패하면 권한 가이드를 표시한다.
- FR-SECRET-007: 조회 성공 시 이름/ARN, Value, 조회 방식과 중계 EC2를 프로필별 SQLite 목록에
  자동 저장하고 등록·수정·삭제를 제공해야 한다. ID와 Value 수정은 SQLite에만 반영해야 한다.
- FR-SECRET-008: ListSecrets 권한이 있으면 선택 상자, 없으면 직접 입력 상자를 제공해야 한다.

### 8.6 S3

- FR-S3-001: Bucket과 Prefix를 저장 위치로 등록할 수 있어야 한다.
- FR-S3-002: `ListAllMyBuckets` 권한 없이도 지정 Bucket을 열 수 있어야 한다.
- FR-S3-003: 단일·다중 파일과 폴더 재귀 업로드를 지원하고 전송 전 선택 목록을 표시해야 한다.
- FR-S3-004: 기존 객체 충돌 시 요약 경고와 덮어쓰기·무시하기·취소를 제공해야 하며 무시하기는
  `sync --no-overwrite`처럼 기존 key를 전송 대상에서 제외해야 한다.
- FR-S3-005: 선택한 단일 객체만 확인 후 삭제하고 prefix 재귀·batch 삭제는 제공하지 않아야 한다.
- FR-S3-006: `ListAllMyBuckets` 성공 시 Bucket 선택 상자를 제공하고 권한 거부 시 직접 입력을
  유지해야 한다.
- FR-S3-007: 선택한 파일과 폴더를 혼합 다중 다운로드하고 상대 경로를 보존해야 하며 실패한
  부분 파일은 남기지 않아야 한다.

### 8.7 설정과 로그

- FR-SETTINGS-001: 로그 저장 경로와 로그 수준을 설정할 수 있어야 한다.
- FR-SETTINGS-002: 로그 파일은 크기 또는 날짜 기준으로 순환해야 한다.
- FR-SETTINGS-003: 로그에 Access Key, Secret Key, Session Token, MFA, DB 비밀번호, SecretString을 기록하지 않아야 한다.
- FR-SETTINGS-004: 진단용 로그 내보내기 전에 민감정보를 다시 마스킹해야 한다.
- FR-SETTINGS-005: 실행 로그 상세에 안전한 오류 코드, AWS action, correlation/operation ID,
  재시도 가능 여부와 사용자가 취할 조치를 표시해야 한다.

### 8.8 CLI와 GUI 공통 실행

- FR-INTERFACE-001: CLI와 GUI는 같은 Domain 및 Application 계층을 사용해야 한다.
- FR-INTERFACE-002: CLI와 GUI 계층에 세션 만료 판정, 토큰 갱신, 권한 판정 및 입력 검증 규칙을 중복 구현하지 않아야 한다.
- FR-INTERFACE-003: 프로필, 인증, EC2, RDS, Secrets Manager와 S3 기능은 각각 독립적인 CLI 하위 명령으로 실행할 수 있어야 한다.
- FR-INTERFACE-004: CLI는 사람이 읽는 기본 출력과 자동 테스트용 JSON 출력을 지원해야 한다.
- FR-INTERFACE-005: CLI는 성공, 입력 오류, 인증 오류, 권한 오류, 네트워크 오류와 실행 오류를 안정적인 종료 코드로 구분해야 한다.
- FR-INTERFACE-006: Access Key, Secret Access Key, Session Token과 MFA 코드는 명령행 인자로 받지 않아야 한다.
- FR-INTERFACE-007: 기능별 GUI 연결은 해당 Application Service의 단위 테스트와 CLI 시나리오 테스트가 통과한 뒤 수행해야 한다.
- FR-INTERFACE-008: Application과 Domain 계층은 콘솔 입출력, 프로세스 종료, GUI 위젯 또는 사용자 메시지 포맷팅에 의존하지 않아야 한다.
- FR-INTERFACE-009: 장시간 작업은 공통 작업 ID, 상태, 진행 이벤트, 취소 요청과 타입이 정의된 결과를 제공해야 한다.
- FR-INTERFACE-010: MFA 필요 상태는 최종 오류로 종료하지 않고 인증 완료 후 원래 요청을 한 번만 자동 재개할 수 있어야 한다.
- FR-INTERFACE-011: RDS 터널과 EC2 외부 터미널은 프로세스 및 SSM 세션 핸들을 통해 실행 상태와 소유권을 추적해야 한다.
- FR-INTERFACE-012: Application 오류에서 CLI 종료 코드와 GUI 표시·복구 동작으로의 변환은 각 presentation의 중앙 mapper에서 처리해야 한다.
- FR-INTERFACE-013: CLI와 GUI는 오류 문자열을 다시 파싱하여 분기하지 않아야 한다.

## 9. 보안 요구사항

- 장기 및 임시 자격증명은 SQLite에 DPAPI 암호문으로만 저장한다.
- 복호화된 자격증명은 필요한 작업 동안만 메모리에 유지한다.
- 데이터베이스와 로그 파일에 현재 Windows 사용자만 접근하도록 파일 권한을 설정한다.
- DB를 다른 Windows 사용자 또는 PC로 복사하면 기존 자격증명을 복호화할 수 없어야 한다.
- Secret 값은 기본 마스킹하고 클립보드 복사는 명시적 동작으로 제한한다.
- 클립보드에 복사한 민감정보는 설정된 시간이 지나면 가능하면 자동 제거한다.
- 서드파티 라이선스와 NOTICE를 배포물에 포함한다.

## 10. 권한 요구사항

기능별로 다음 AWS 권한이 필요하며, 없는 권한 때문에 관련 없는 기능까지 중단되어서는 안 된다.

| 기능 | 대표 권한 |
|---|---|
| 인증 확인 | `sts:GetCallerIdentity` |
| MFA 세션 발급 | `sts:GetSessionToken` |
| EC2 표시 보강 | `ec2:DescribeInstances` |
| EC2 시작·재부팅(선택) | `ec2:StartInstances`, `ec2:RebootInstances` |
| SSM 목록·접속 | `ssm:DescribeInstanceInformation`, `ssm:StartSession` |
| EC2 경유 Secret 조회(선택) | 앱 사용자: `ssm:SendCommand`, `ssm:GetCommandInvocation`; EC2 instance role: `secretsmanager:GetSecretValue` |
| EC2 터미널 Secret 조회 fallback(선택) | 앱 사용자: `ssm:StartSession`, `ssm:TerminateSession`; EC2 instance role: `secretsmanager:GetSecretValue` |
| RDS 자동 가져오기(선택) | `rds:DescribeDBInstances` |
| Secret 조회 | `secretsmanager:GetSecretValue` |
| Secret 목록(선택) | `secretsmanager:ListSecrets` |
| Secret 키-값 변경(선택) | `secretsmanager:PutSecretValue` |
| S3 목록·업로드·단건 삭제 | `s3:ListBucket`, `s3:PutObject`, `s3:DeleteObject` |
| S3 Bucket catalog(선택) | `s3:ListAllMyBuckets` |

## 11. 오류 처리 요구사항

- AWS 오류 코드를 사용자 메시지로 변환한다.
- 권한 부족 시 필요한 IAM Action을 표시한다.
- 네트워크, 인증, 대상 오프라인, 포트 충돌, 플러그인 실행 실패를 구분한다.
- 실패한 작업의 최소 범위만 중단하고 애플리케이션은 계속 사용할 수 있어야 한다.
- 예외를 무시하거나 로그만 기록하고 성공으로 처리해서는 안 된다.

## 12. 성공 기준

- 신규 사용자가 ZIP 압축 해제 후 10분 이내에 첫 AWS 프로필을 등록할 수 있다.
- 유효한 기존 토큰이 있으면 MFA 입력 없이 EC2 또는 RDS 세션을 시작할 수 있다.
- 만료된 토큰은 한 번의 MFA 입력으로 갱신된다.
- 저장된 RDS 터널 세션은 3번 이하의 사용자 동작으로 시작된다.
- gossm, AWS CLI, Python이 설치되지 않은 Windows 테스트 PC에서 실행된다.
- 인증정보는 DB 평문과 로그에서 발견되지 않고 Secret 원문은 로그에서 발견되지 않는다.
  저장 Secret Value는 사용자 승인에 따라 현재 사용자 전용 SQLite에만 존재한다.
- 공통 및 기능별 Application Service를 GUI 없이 CLI에서 실행하고 성공·실패 시나리오를 검증할 수 있다.
- 동일한 유스케이스의 CLI와 GUI 실행 결과가 같은 도메인 규칙과 오류 코드에 따라 처리된다.
- 장시간 작업의 진행·취소, MFA 재개와 프로세스 정리가 CLI와 GUI에서 동일한 Application 계약으로 동작한다.

## 13. MVP 범위

### 포함

- AWS 프로필과 DPAPI 암호화 저장
- MFA Session Token 발급·재사용
- EC2 SSM 목록·접속
- RDS 저장 터널 세션 CRUD 및 실행
- 설정 및 순환 로그
- 개발·진단용 기능별 CLI
- Portable ZIP 패키징

### 후속 기능

- Secrets Manager 조회
- S3 목록, 업로드 및 단일 객체 삭제
- RDS instance endpoint 자동 가져오기
- 업데이트 확인 및 새 버전 배포

## 14. 미결정 사항

- 자동 업데이트를 제공할지 수동 ZIP 교체 방식으로 유지할지
- 여러 프로필에서 동일한 RDS 터널 세션을 공유할지
- S3 다운로드와 폴더 업로드를 MVP에 포함할지
- 마스터 비밀번호 기반의 PC 간 자격증명 이동 기능을 제공할지


### S3 업로드/다운로드 UI 보완 (2026-09-17)

- 업로드 파일 목록 배경에서 Qt 선택기를 직접 열어 파일·폴더를 한 창에서 선택한다.
- 카드에는 한 줄 이름, 가능한 이미지 썸네일, 수정 시간과 파일 크기를 표시한다. 폴더는 전용 아이콘을 사용한다.
- 다운로드 대상에 기존 파일이 있으면 덮어쓰기·무시·취소를 선택하며, 미승인 덮어쓰기를 금지한다. 완료 안내에 다운로드/무시 개수를 표시한다.
