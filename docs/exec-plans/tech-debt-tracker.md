# Technical Debt Tracker

| ID | Status | Area | Debt | Impact | Owner/plan |
|---|---|---|---|---|---|
| PKG-001 | External verification | Supply chain | 승인된 공식 x64 Session Manager Plugin binary, SHA-256, version, LICENSE/NOTICE를 확보하고 `approved=true`, `test_only=false` manifest로 production build를 실행한다. | TEST-ONLY ZIP은 출시할 수 없음 | Phase 6 release gate |
| PKG-002 | External verification | Package | Python/AWS CLI/gossm/Plugin이 없는 깨끗한 Windows 10/11 VM의 한글·공백 및 read-only 경로에서 production ZIP, GUI/CLI/doctor와 10분 내 첫 프로필 등록을 확인한다. | 현재 로컬 packaged smoke는 clean-VM 설치 상태까지 증명하지 않음 | Phase 6 release gate |
| PKG-003 | External verification | CI | 원격 저장소 push 후 Windows package job, TEST-ONLY smoke와 build diagnostics artifact 보존을 확인한다. | CI 구성은 로컬에서만 정적 검토됨 | Phase 6 CI gate |
| AWS-001 | External verification | AWS smoke | 승인된 최소 권한 non-production profile/resources로 인증, EC2와 RDS 실제 smoke 및 Plugin 최종 경계를 확인한다. | AWS/Plugin 실제 상호운용은 fake/Stubber 검증만 완료 | `scripts/test-aws.ps1` |
| AWS-002 | External verification | Secrets Manager | `secretsmanager:GetSecretValue`만 허용하고 `ListSecrets`는 거부한 non-production 프로필에서 `./scripts/test-aws.ps1 -Profile <profile> -SecretId <name-or-arn>`을 실행해 직접 조회와 기본 마스킹을 확인한다. 별도의 List 권한 프로필에서는 GUI 선택 목록도 확인한다. | 실제 IAM 권한 조합과 서비스 상호운용은 Stubber로만 검증됨 | Phase 7 release gate |
| AWS-003 | External verification | S3 | `s3:ListBucket`와 대상 prefix의 `s3:PutObject`, multipart 권한만 가진 non-production 프로필로 `./scripts/test-aws.ps1 -Profile <profile> -S3Bucket <bucket> -S3Prefix <prefix>`를 실행한다. 작은 테스트 파일과 16 MiB 이상 파일은 먼저 `-S3UploadFile <paths>`로 preview한 뒤 `-S3Upload`로 업로드하고, 기존 key는 별도 `-S3Overwrite`에서만 덮어쓴다. 업로드 중 취소 후 incomplete multipart가 남지 않았는지 S3 inventory/API로 확인한다. | 실제 IAM, 대용량 네트워크, 서비스측 multipart 정리는 Stubber/local fake만 검증됨 | Phase 8 release gate |
| QA-001 | Resolved 2026-10-13 | Local gates | 문서 검사를 프로젝트 권위 문서 범위로 정정하고 외부 캐시 제외 회귀 테스트 추가; 잔여 혼합 줄바꿈 정규화. | check.ps1 전체 통과: 693 passed, 1 skipped; 문서/포맷/보안/의존성 감사 통과 | 005 계획 및 `.qa-s3-drives/check-final-candidate.log` |
| QA-002 | Resolved 2026-09-18 | Existing S3 tests | 2026-09-17 전체 pytest에서 test_s3.py의 selected_files_and_folders_download_to_one_selected_root 및 upload_sources_accumulate_remove_clear_and_switch_single_action 실패 확인. | 신규 S3 표 UI 계약 반영 후 해당 테스트 포함 S3 80 passed, 1 skipped | Phase 9 S3 후속 검증 |
| QA-003 | Resolved 2026-09-21 | Qt full-suite isolation | 테스트마다 top-level QWidget 및 자식 timer를 DeferredDelete로 정리하고 QApplication 수명을 session fixture로 유지. RDS→S3 충돌 재현 후 해결 확인. | GUI 117 passed, 전체 528 passed/1 skipped로 native crash 없이 종료 | Phase 9 CI 수정; `.test-ci-full-final.log` |
| QA-004 | Resolved 2026-09-21 | RDS static scan | 안내 widget 초기화 assert 2건을 기존 Secrets 방식의 명시적 RuntimeError로 교체. | 전체 Bandit exit 0 | Phase 9 CI 수정 |


## QA-001 recheck — 2026-10-07 RDS duration
- Owner remains Phase 9 follow-up. `004-rds-session-duration.md` records current evidence:
  check.ps1 documentation still fails on vendor/graft links; full format has one unchanged
  mixed-line-ending file, `src/aws_connect/infrastructure/aws_identity_gateway.py`.
- Feature tests and independent static/security gates pass; these unrelated gate failures are
  not hidden or repaired by changing user work. Next: fix the existing gate scope/line endings
  and rerun the full check before declaring repository release readiness.

## Distribution follow-up — 2026-10-13

- QA-001은 005의 전체 검사로 해결했다. 위 2026-10-07 기록은 과거 증거다.
- PKG-002: 사용자가 VM 없음으로 답변하여 이번 범위는 로컬 ZIP/Setup 후보와 검증 안내까지다.
  깨끗한 Windows 10/11의 UAC 취소/다른 관리자 계정/재부팅/업데이트/제거 증거는 미확보다.
- PKG-003: 후보 생성 및 동일 바이트 공개 workflow는 구현했다. 원격 실행 검증과 GitHub
  `release` environment의 required reviewers 설정은 공개 전 필수 외부 작업이다.
- 이번 작업은 WinFsp 변경 설치, AWS 호출, 원격 push/공개를 수행하지 않았다.
