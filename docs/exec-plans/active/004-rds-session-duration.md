# RDS tunnel maximum session duration

## Scope and acceptance
- Extend saved tunnels with 1/8/12/24 hours, default 12; preserve omitted updates, rename and clone.
- Migration 13 upgrades existing records; generated schema stays synchronized.
- Each successful connection starts a fresh lifetime. GUI polls every 2 seconds and expires
  after resume from sleep; CLI uses a bounded foreground wait. No persisted running state.
- Cleanup ownership is serialized across poll/manual stop/shutdown; process/EndSession once.
- Typed `max_duration` termination, nonmodal GUI notice, CLI output, stop history metadata.
- Preserve existing user edits; no AWS Preferences/IAM/RDS/S3 changes or real AWS tests.

## Authorities and implementation
- Product: ../../product-specs/aws-connect.md
- UI: ../../DESIGN.md and ../../FRONTEND.md
- Reliability: ../../RELIABILITY.md; security: ../../SECURITY.md
- Domain -> saved service/SQLite -> SSM lifecycle/RDS -> CLI -> GUI -> tests and docs.
- Selected: existing polling/process contracts (small change, existing cleanup reused).
  Alternatives: scheduler thread adds ownership races; AWS Preferences affect unrelated sessions.
- Runtime effects: new SQLite column, bounded process wait, terminate/kill and EndSession at
  expiry; no new thread, port, dependency or AWS permission. Distribution needs EXE/ZIP rebuild.

## Progress
- 2026-10-07: inspected graft map/source, active 001/003 plans and dirty working tree.
  Python 3.12/PySide6, migrations currently 12. Existing QA-001 documentation gate failure.
- SSOT search: TunnelSession, SaveTunnelSessionRequest, session_duration_hours,
  active_tunnels, ForegroundSsmSessionRunner. Reuse existing paths; auth permits 36 separately.
- Next: implement contracts, persistence and lifetime, then adapters/tests.

## Verification
Pending targeted domain/application/SQLite/CLI/GUI/history tests, Ruff/format/mypy,
architecture/imports/error mappers, generated docs, secret scan and scripts/check.ps1.
No real AWS execution is authorized for a specific profile/resource in this session.


## Implementation and evidence (2026-10-07)
- Implemented domain choices/default/strict validation, optional save request with preserved
  updates/clone/rename, migration 13, CRUD mapping and generated schema.
- Extended ForegroundSsmSessionRunner with optional bounded launch/wait and typed
  SessionEndReason.MAX_DURATION. The existing managed terminate/kill function is shared;
  unbounded EC2 plugin.run stays unchanged. Timeout cleanup errors never become success.
- RDS records a fresh UTC deadline immediately after managed launch; ownership lock serializes
  status, stop and shutdown through cleanup. Expiration reuses logged stop, emits one terminal
  result and releases ownership. Explicit stop after a completed owner remains not-found.
- GUI adds domain-driven duration selector, default/restore/dirty/save-before-start/lock,
  persistent expiry state, toast and active-list/dashboard removal. Poll observes ALL profiles
  so switching profile cannot suspend expiry; display filters remain profile-scoped. Polls do
  not overlap, and other-profile cleanup failures still surface. No new timer/thread.
- CLI create/update parser and JSON/text/list/termination output expose duration/reason.
  Execution-log sanitizer now accepts reason; SQLite history integration verifies retention.
- Existing endpoint selection and other user changes were preserved. No real AWS, IAM, RDS,
  Session Manager Preferences or user database operations were performed.

### Commands and results
Python executable below is `.venv/Scripts/python.exe` (commands run from repository root).
- Targeted run:
  `python -m pytest tests/unit/domain/test_tunnel_session.py tests/unit/application/test_rds_tunnel_service.py tests/unit/application/test_ssm_session.py tests/unit/application/test_managed_ssm_session.py tests/integration/infrastructure/test_sqlite_profile_store.py tests/integration/infrastructure/test_execution_logs.py tests/adapter/cli/test_rds_cli.py tests/adapter/gui/test_ec2_rds.py tests/unit/infrastructure/test_session_manager_plugin.py --no-cov -q --basetemp=.test-rds-duration3 -o cache_dir=.test-cache-rds-duration`
  -> **194 passed**. Earlier runs found old expected schema/request values and missing text/history
  output; those were corrected before this successful run.
- `python -m pytest --basetemp=.test-rds-duration-full -o cache_dir=.test-cache-rds-duration-full`
  -> **648 passed, 1 skipped; coverage 86.88%**. Output `.rds-duration-tests.log`.
- Final cross-profile error/shutdown guard and ownership-lock cleanup regression:
  `python -m pytest tests/unit/application/test_rds_tunnel_service.py tests/adapter/gui/test_ec2_rds.py tests/adapter/test_error_mapper_completeness.py tests/adapter/test_presentation_consistency.py --no-cov -q --basetemp=.test-rds-duration-final -o cache_dir=.test-cache-rds-duration`
  -> **57 passed**. Added one GUI error-surfacing test after the full-suite snapshot.
- `python -m ruff check src tests tools` -> passed.
- `python -m ruff format --check` for all 17 changed Python files -> passed.
  Full `python -m ruff format --check src tests tools` -> **1 pre-existing failure**:
  `src/aws_connect/infrastructure/aws_identity_gateway.py` mixed line endings; 175 files formatted.
  That file is unchanged by this effort. Evidence `.rds-duration-format.log`.
- `python -m mypy` -> **97 source files passed**.
- `python tools/architecture_check.py` -> passed; `.venv/Scripts/lint-imports.exe` -> **2 contracts kept**.
- `python tools/verify_critical_coverage.py coverage.xml` -> **5 files at 100%, passed**.
- `python -m bandit -q -r src` -> exit 0; existing nosec comment warnings only.
- `python -m detect_secrets scan` with the enumerated `src/tests/tools/**/*.py` files
  -> **174 Python files, 0 findings** (JSON results checked without printing secret values).
- `python tools/generate_db_schema.py docs/generated/db-schema.md` -> generated;
  Python equality assertion against `tools.generate_db_schema.render()` -> passed.
- `python -m aws_connect.cli_main rds session create --help` and `update --help` -> exit 0,
  both expose `--session-duration-hours {1,8,12,24}` without opening application state.
- `python tools/render_gui.py .qa-rds-duration --width 1424 --height 894` -> exit 0;
  inspected `.qa-rds-duration/rds.png`: selector/default and action layout render correctly.
- `graft build` -> passed; graph refreshed from source.
- `./scripts/check.ps1` -> **failed at documentation** (exit 1); existing vendor/graft broken
  links, QA-001. Evidence `.rds-duration-check.log`. Its later steps did not run in that script;
  independent gate results are recorded above. Full release gate is NOT claimed passed.

### Status / next step
Feature implementation and focused acceptance tests pass. This plan remains active solely because
repository-wide documentation/format gates have existing QA-001 failures. Resolve QA-001 separately,
then rerun `scripts/check.ps1` before moving the plan to completed. Product/DESIGN/FRONTEND/
RELIABILITY/SECURITY and generated schema match the change. No new product debt accepted.
Restart the development app to load changes; migration runs automatically at app startup.
EXE/ZIP distribution requires a rebuild. Real AWS smoke testing requires an explicitly approved
non-production profile/resource, and has not been performed here.

- Final timing refinement: capture connection time before waiting for the ownership lock so
  unrelated cleanup latency cannot extend a newly connected tunnel lifetime.
  `python -m pytest tests/unit/application/test_rds_tunnel_service.py --no-cov -q --basetemp=.test-rds-duration-clock -o cache_dir=.test-cache-rds-duration`
  -> **23 passed**; changed service Ruff format reapplied.

## Repository inclusion follow-up — 2026-10-13

- 005 작업의 `check.ps1` 전체 검사(693 passed, 1 skipped)로 기존 QA-001 문서/포맷 문제가 해결됐다.
- 이 문서는 로컬에만 존재했으나 tracked 001 계획의 링크 대상이다. CI 문서 누락을 수정하기 위해
  repository에 포함한다. 기존 RDS 구현을 다시 변경하지 않는다.
- `scripts/verify-docs.ps1` 및 tracked index만 export한 snapshot의 `tools/verify_docs.py` 모두 통과.
  실환경 AWS/깨끗한 VM 검증은 이 문서 추가로 완료된 것으로 간주하지 않는다.
