from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
from tests.unit.application.test_secrets_service import build_service

from aws_connect.application.activity_log_service import ActivityLogQuery, ExecutionLogService
from aws_connect.application.operations import OperationCancelled, OperationContext
from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.domain.execution_log import (
    ErrorCategory,
    ExecutionLevel,
    ExecutionLogEvent,
    ExecutionLogFilter,
    ExecutionPhase,
    ExecutionResult,
)
from aws_connect.infrastructure.aws_identity_gateway import translate_aws_error
from aws_connect.infrastructure.execution_log_masking import MaskedExecutionLogSanitizer
from aws_connect.infrastructure.logging_setup import RotatingLogConfigurator
from aws_connect.infrastructure.managed_logs import (
    MaskedManagedLogReader,
    StructuredActivityEventWriter,
)
from aws_connect.infrastructure.sqlite_profile_store import SqliteProfileStore


@pytest.fixture
def history(tmp_path: Path):
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path / "logs", LogLevel.WARNING)
    clock = Mock()
    clock.now.side_effect = lambda: datetime.now(UTC)
    store = SqliteProfileStore(tmp_path / "state.db")
    logger = logging.getLogger("aws_connect")
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = []
    RotatingLogConfigurator().apply(settings.get())
    service = ExecutionLogService(
        settings,
        MaskedManagedLogReader(),
        StructuredActivityEventWriter(),
        clock,
        repository=store,
        sanitizer=MaskedExecutionLogSanitizer(),
    )
    yield service, store, tmp_path
    for handler in logger.handlers:
        handler.close()
    logger.handlers = old_handlers
    logger.setLevel(old_level)
    logger.propagate = old_propagate


def event(result: ExecutionResult = ExecutionResult.SUCCESS) -> ExecutionLogEvent:
    return ExecutionLogEvent(
        datetime.now(UTC),
        ExecutionLevel.INFO,
        result,
        ExecutionPhase.COMPLETED,
        "ec2",
        "list",
        "i-example",
        "조회 완료",
        "correlation-example",
        "operation-example",
        aws_action="DescribeInstances",
    )


@pytest.mark.parametrize(
    "result,level",
    [
        (ExecutionResult.SUCCESS, ExecutionLevel.INFO),
        (ExecutionResult.WARNING, ExecutionLevel.WARNING),
        (ExecutionResult.FAILURE, ExecutionLevel.ERROR),
        (ExecutionResult.CANCELLED, ExecutionLevel.INFO),
    ],
)
def test_sqlite_and_file_preserve_independent_level_result_and_identity(history, result, level):
    service, store, root = history
    saved = replace(event(result), level=level, aws_request_id="request-example")
    service.record(saved)
    assert service.list_recent() == [saved]
    assert SqliteProfileStore(store.path).query_execution_logs(ExecutionLogFilter(), 5) == [saved]
    text = (root / "logs" / "aws-connect.log").read_text(encoding="utf-8")
    assert "correlation-example" in text and "operation-example" in text
    assert result.value in text and "request-example" in text
    assert service.recent()[0].level == level.value


@pytest.mark.parametrize(
    "query",
    [
        ActivityLogQuery(period_days=1, levels=("ERROR",)),
        ActivityLogQuery(period_days=1, features=("ec2",)),
        ActivityLogQuery(levels=("ERROR",), features=("ec2",)),
        ActivityLogQuery(search="operation-example", features=("ec2",)),
        ActivityLogQuery(errors_only=True, period_days=1),
        ActivityLogQuery(
            period_days=1,
            levels=("ERROR",),
            features=("ec2",),
            search="DescribeInstances",
            errors_only=True,
        ),
    ],
)
def test_combined_filters_are_applied_before_limit(history, query):
    service, _, _ = history
    selected = replace(event(ExecutionResult.FAILURE), level=ExecutionLevel.ERROR)
    service.record(selected)
    for _ in range(5):
        service.record(
            replace(event(), feature="s3", operation_id="another", aws_action="ListObjectsV2")
        )
    rows = service.recent(1, query)
    assert len(rows) == 1 and rows[0].feature == "ec2"
    assert service.list(ExecutionLogFilter(search="correlation-example"))


def test_masks_metadata_and_exception_text_before_sqlite_and_file(history):
    service, store, root = history
    sensitive = os.urandom(24).hex()
    service.record(
        replace(
            event(),
            message=f"password={sensitive}",
            metadata_json=json.dumps(
                {
                    "password": sensitive,
                    "SessionToken": sensitive,
                    "raw_response": sensitive,
                    "instance_id": "i-example",
                    "file_size": 123,
                }
            ),
        )
    )
    row = service.list_recent()[0]
    assert sensitive not in row.message + row.metadata_json
    assert "i-example" in row.metadata_json and "123" in row.metadata_json
    with sqlite3.connect(store.path) as db:
        assert sensitive not in str(db.execute("SELECT * FROM execution_logs").fetchall())
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert sensitive not in (root / "logs" / "aws-connect.log").read_text(encoding="utf-8")


def test_retention_bounds_days_rows_and_recent_excludes_incomplete(history, monkeypatch):
    service, store, _ = history
    monkeypatch.setattr("aws_connect.infrastructure.sqlite_profile_store.EXECUTION_LOG_MAX_ROWS", 5)
    service.record(replace(event(), occurred_at=datetime.now(UTC) - timedelta(days=31)))
    for number in range(8):
        service.record(replace(event(), target=str(number)))
    service.record(replace(event(), phase=ExecutionPhase.STARTED))
    assert len(service.list()) == 5
    assert all(row.phase is ExecutionPhase.COMPLETED for row in service.list_recent())
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM execution_logs").fetchone()[0] == 5


@pytest.mark.parametrize(
    "code,category",
    [
        ("AccessDenied", ErrorCategory.PERMISSION),
        ("ThrottlingException", ErrorCategory.NETWORK),
        ("TargetNotConnected", None),
    ],
)
def test_aws_error_classification_and_request_id(history, code, category):
    service, _, _ = history
    error = translate_aws_error(
        ClientError(
            {"Error": {"Code": code}, "ResponseMetadata": {"RequestId": "aws-request-example"}},
            "ListObjectsV2",
        ),
        service="s3",
        action="ListObjectsV2",
    )
    if category is not None:
        assert error.error_category is category
    assert error.aws_request_id == "aws-request-example"
    assert error.required_permission == ("s3:ListBucket" if code == "AccessDenied" else None)
    secrets, gateway = build_service("{}")
    secrets._activity_logs = service
    gateway.get_secret_value.side_effect = error
    with pytest.raises(type(error)):
        secrets.get("test/item")
    row = service.list_recent()[0]
    assert row.result is ExecutionResult.FAILURE and row.error_code == code
    assert row.aws_request_id == "aws-request-example"


def test_real_use_case_success_failure_cancel_and_no_secret_payload(history):
    service, _, root = history
    raw = os.urandom(24).hex()
    secrets, gateway = build_service(raw)
    secrets._activity_logs = service
    context = OperationContext(operation_id="operation-test", correlation_id="correlation-test")
    secrets.get("test/item", context=context)
    rows = service.list()
    assert {row.correlation_id for row in rows} == {"correlation-test"}
    assert {row.operation_id for row in rows} == {"operation-test"}
    assert service.list_recent()[0].result is ExecutionResult.SUCCESS
    gateway.get_secret_value.side_effect = OperationCancelled
    with pytest.raises(OperationCancelled):
        secrets.get("test/item", context=context)
    assert service.list_recent()[0].result is ExecutionResult.CANCELLED
    assert raw not in (root / "logs" / "aws-connect.log").read_text(encoding="utf-8")
    assert all(raw not in row.message + row.metadata_json for row in service.list())


def test_network_and_timeout_are_never_permission_errors():
    for source, category in (
        (EndpointConnectionError(endpoint_url="https://example.invalid"), ErrorCategory.NETWORK),
        (ReadTimeoutError(endpoint_url="https://example.invalid"), ErrorCategory.TIMEOUT),
    ):
        error = translate_aws_error(source, service="ssm", action="StartSession")
        assert error.error_category is category
        assert error.required_permission is None


def test_successful_sts_response_request_id_reaches_sqlite(history, monkeypatch):
    from tests.integration.infrastructure.test_aws_identity_gateway import (
        credentials,
        stubbed_gateway,
    )

    from aws_connect.application.execution_context import execution_scope

    service, _, _ = history
    gateway, stubber = stubbed_gateway(monkeypatch)
    stubber.add_response(
        "get_caller_identity",
        {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/developer",
            "UserId": "fixture-user",
            "ResponseMetadata": {"RequestId": "successful-aws-request"},
        },
    )
    with stubber, execution_scope("user-request", "sts-operation"):
        gateway.get_identity(credentials(), "ap-northeast-2")
        service.record(feature="auth", operation="validate", message_code="auth.validate.completed")
    row = service.list_recent()[0]
    assert (row.aws_service, row.aws_action, row.aws_request_id) == (
        "sts",
        "GetCallerIdentity",
        "successful-aws-request",
    )
    assert row.correlation_id == "user-request"


@pytest.mark.parametrize("explicit_cancel", [False, True])
def test_mfa_cancellation_is_logged_once_without_issuing_credentials(
    history: tuple[ExecutionLogService, SqliteProfileStore, Path],
    tmp_path: Path,
    explicit_cancel: bool,
) -> None:
    from tests.unit.application.test_profile_authentication import build_services

    from aws_connect.application.authentication_service import OperationCoordinator
    from aws_connect.application.operations import OperationState

    service, _, _ = history
    store, clock, gateway, _, authentication, profile = build_services(tmp_path / "auth")
    authentication._activity_logs = service
    coordinator = OperationCoordinator(authentication, clock)
    challenge = coordinator.start_refresh(profile.id)
    assert challenge.state is OperationState.MFA_REQUIRED

    if explicit_cancel:
        coordinator.cancel(challenge.operation_id)
    else:
        assert coordinator.resume(challenge.operation_id, None).state is OperationState.CANCELLED
    coordinator.cancel(challenge.operation_id)
    assert coordinator.resume(challenge.operation_id, "123456").state is OperationState.FAILED

    cancelled = [row for row in service.list() if row.result is ExecutionResult.CANCELLED]
    assert len(cancelled) == 1
    assert cancelled[0].operation_id == challenge.operation_id
    assert cancelled[0].correlation_id
    assert cancelled[0].result is ExecutionResult.CANCELLED
    assert gateway.refresh_calls == 0
    assert store.get_session(profile.id) is None


def test_mfa_resume_keeps_the_original_user_correlation(history, tmp_path):
    from tests.unit.application.test_authenticated_operation import build

    from aws_connect.application.operations import OperationState

    service, _, _ = history
    _, _, _, profiles, auth, refreshes, operations, profile = build(tmp_path / "auth")
    auth._activity_logs = service
    refreshes._activity_logs = service
    secrets, gateway = build_service("{}")
    secrets._profiles = profiles
    secrets._sessions = auth.session_guard
    secrets._activity_logs = service
    context = OperationContext(correlation_id="original-user-request")
    initial = operations.start_long(
        profile.id, lambda current: secrets.get("example", profile.id, context=current), context
    )
    assert initial.state is OperationState.MFA_REQUIRED
    resumed = operations.resume(initial.operation_id, "".join(str(i) for i in range(6)))
    assert resumed.state is OperationState.SUCCEEDED
    assert {row.correlation_id for row in service.list()} == {"original-user-request"}
    assert service.list_recent()[0].result is ExecutionResult.SUCCESS
    assert not any(row.result is ExecutionResult.FAILURE for row in service.list())


def test_existing_tracebacks_are_kept_but_credential_text_is_masked(history):
    _, _, root = history
    sensitive = os.urandom(20).hex()
    try:
        raise RuntimeError(f"password={sensitive}")
    except RuntimeError:
        logging.getLogger("aws_connect.diagnostic").exception("diagnostic failure")
    text = (root / "logs" / "aws-connect.log").read_text(encoding="utf-8")
    assert "Traceback" in text and "RuntimeError" in text
    assert sensitive not in text


def test_real_ec2_s3_and_settings_successes_use_the_same_store(history):
    from tests.unit.application.test_ec2_service import build_service as ec2_service
    from tests.unit.application.test_s3_service import _services

    from aws_connect.application.settings_service import SettingsService, UpdateSettingsRequest

    service, store, root = history
    ec2, _, _, _ = ec2_service()
    ec2._activity_logs = service
    ec2.list_targets()
    ec2.connect("i-online")
    _, s3, _, gateway = _services()
    s3._activity_logs = service
    s3.list_objects("example-bucket")
    s3.download_object("example-bucket", "example.txt", root / "download.txt")
    s3.delete_object("example-bucket", "example.txt")
    secrets, _ = build_service("{}")
    secrets._activity_logs = service
    secrets.list()
    settings = SettingsService(store, root / "logs")
    settings.bind_execution_log(service)
    settings.update(UpdateSettingsRequest(log_level="INFO"))
    rows = service.list_recent(10)
    assert {(row.feature, row.action) for row in rows} >= {
        ("ec2", "list"),
        ("s3", "list"),
        ("s3", "download"),
        ("s3", "delete_object"),
        ("secrets", "list"),
        ("program", "settings_update"),
    }
    assert all(row.result is ExecutionResult.SUCCESS for row in rows)
    assert service.load_dashboard_recent(3) == rows[:3]


def test_managed_rds_start_stop_and_s3_transfer_warning_retry(history, tmp_path):
    from tests.unit.application.test_rds_tunnel_service import build_tunnel_service
    from tests.unit.application.test_s3_service import _services

    from aws_connect.application.operations import OperationState
    from aws_connect.application.rds_tunnel_service import StartTunnelRequest
    from aws_connect.application.s3_service import UploadConflictPolicy
    from aws_connect.application.ssm_session import ManagedSsmSession

    service, _, _ = history
    rds, _, _, _, _ = build_tunnel_service()
    rds._activity_logs = service
    managed = Mock()
    managed.start.return_value = ManagedSsmSession(
        "rds-operation", 100, "session-example", OperationState.RUNNING
    )
    managed.stop.return_value = ManagedSsmSession(
        "rds-operation", 100, "session-example", OperationState.CANCELLED, 0
    )
    rds._managed_runner = managed
    rds.start_managed(StartTunnelRequest("dev-db", "dev"))
    rds.stop("rds-operation")
    assert {row.action for row in service.list_recent()} >= {"start", "stop"}
    start = next(row for row in service.list_recent() if row.action == "start")
    assert json.loads(start.metadata_json)["local_port"] == 13306
    _, s3, _, gateway = _services()
    s3._activity_logs = service
    source = tmp_path / "example.txt"
    source.write_text("example", encoding="utf-8")
    plan = s3.prepare_upload([source], "example-bucket")
    s3.upload(plan, context=OperationContext(attempt=2), policy=UploadConflictPolicy.SKIP_EXISTING)
    assert any(
        row.action == "retry" and row.phase is ExecutionPhase.PROGRESS for row in service.list()
    )
    gateway.object_exists.return_value = True
    skipped_plan = s3.prepare_upload([source], "example-bucket")
    s3.upload(skipped_plan, context=OperationContext(), policy=UploadConflictPolicy.SKIP_EXISTING)
    assert service.list_recent()[0].result is ExecutionResult.WARNING


def test_query_values_cannot_change_sql_and_debug_events_persist(history):
    service, _, _ = history
    service.record(replace(event(), level=ExecutionLevel.DEBUG))
    assert service.list(ExecutionLogFilter(levels=("DEBUG",)))
    for query in (
        ExecutionLogFilter(search="' OR 1=1 --"),
        ExecutionLogFilter(features=("' OR 1=1 --",)),
    ):
        assert service.list(query) == []


def test_concurrent_worker_writes_do_not_lose_events(history):
    from concurrent.futures import ThreadPoolExecutor

    service, _, _ = history
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(
            workers.map(
                lambda i: service.record(replace(event(), operation_id=f"op-{i}")), range(20)
            )
        )
    assert len(service.list()) == 20
    assert len({row.operation_id for row in service.list()}) == 20


@pytest.mark.parametrize("search", ["", "test/item", "operation-gui", "correlation-gui"])
def test_successful_use_case_reaches_gui_and_refresh_preserves_filters(history, search):
    from tests.adapter.gui.test_logs import ImmediateTaskRunner, _app

    from aws_connect.presentation.gui.logs import LogsSettingsPage

    app = _app()
    service, _, root = history
    secrets, _ = build_service("{}")
    secrets._activity_logs = service
    secrets.get(
        "test/item",
        context=OperationContext(operation_id="operation-gui", correlation_id="correlation-gui"),
    )
    settings = Mock()
    settings.get.return_value = AppSettings(root / "logs", LogLevel.WARNING)
    page = LogsSettingsPage(settings, service, Mock(), ImmediateTaskRunner())
    page.level_filter.setCurrentText("INFO")
    page.feature_filter.setCurrentText("Secrets")
    page.search.setText(search)
    page.refresh()
    app.processEvents()
    assert any(entry.result == "SUCCESS" and entry.level == "INFO" for entry in page._entries)
    expected = page._query()
    page.refresh_button.click()
    assert page._query() == expected
    assert page.entries.rowCount() > 0
    page.close()


def test_s3_delete_permission_uses_scoped_real_service_result_not_other_api_errors(history):
    from tests.unit.application.test_s3_service import _services

    from aws_connect.application.dashboard_service import DashboardService, PermissionState
    from aws_connect.domain.errors import AwsPermissionError

    logs, _, _ = history
    _, s3, _, gateway = _services()
    s3._activity_logs = logs
    clock = Mock()
    clock.now.side_effect = lambda: datetime.now(UTC)
    dashboard = DashboardService(
        s3._profiles, s3._sessions, logs, clock, Mock(), Mock(), Mock(), Mock()
    )
    selected = s3._profiles.resolve(None)

    def deletion_state():
        result = dashboard.check_permissions(selected.require_id())
        return next(
            p.state
            for f in result.features
            if f.feature == "s3"
            for p in f.permissions
            if p.key == "delete"
        )

    # No DeleteObject evidence is unknown, never a denial inferred from S3 feature alone.
    logs.record(
        feature="s3",
        operation="list",
        result="FAILURE",
        level="ERROR",
        aws_service="ssm",
        aws_action="DescribeInstanceInformation",
        error_category=ErrorCategory.PERMISSION,
        profile_id=selected.require_id(),
        region=selected.region,
    )
    assert deletion_state() is PermissionState.UNKNOWN
    s3.delete_object("example-bucket", "example.txt")
    success = logs.list_recent()[0]
    assert success.aws_action == "DeleteObject"
    assert json.loads(success.metadata_json)["profile_id"] == selected.require_id()
    assert json.loads(success.metadata_json)["bucket"] == "example-bucket"
    assert deletion_state() is PermissionState.ALLOWED

    gateway.delete_object.side_effect = AwsPermissionError(
        "aws.permission.denied", "AccessDenied", aws_service="s3", aws_action="DeleteObject"
    )
    with pytest.raises(AwsPermissionError):
        s3.delete_object("restricted-bucket", "example.txt")
    # The same key in different buckets must not overwrite successful evidence.
    assert deletion_state() is PermissionState.UNKNOWN
    failures = [
        e
        for e in logs.list_recent()
        if e.aws_action == "DeleteObject" and e.result is ExecutionResult.FAILURE
    ]
    assert len(failures) == 1


@pytest.mark.parametrize("multipart", [False, True])
def test_actual_s3_write_rename_and_delete_feed_dashboard(history, tmp_path, multipart):
    from tests.unit.application.test_s3_service import _services

    from aws_connect.application.dashboard_service import DashboardService, PermissionState
    from aws_connect.application.s3_service import MULTIPART_THRESHOLD, UploadConflictPolicy
    from aws_connect.domain.errors import AwsPermissionError

    logs, _, _ = history
    _, s3, _, gateway = _services()
    s3._activity_logs = logs
    source = tmp_path / "sample.txt"
    source.write_text("fixture", encoding="utf-8")
    plan = s3.prepare_upload([source], "example-bucket", "reports/")
    if multipart:
        plan = replace(plan, items=(replace(plan.items[0], size=MULTIPART_THRESHOLD),))
    s3.upload(plan, policy=UploadConflictPolicy.OVERWRITE, context=OperationContext())
    expected_action = "CompleteMultipartUpload" if multipart else "PutObject"
    assert any(e.aws_action == expected_action for e in logs.list_recent())
    s3.rename_object("example-bucket", "reports/sample.txt", "renamed.txt")
    s3.delete_object("example-bucket", "reports/renamed.txt")
    s3.list_objects("example-bucket", "reports/")
    gateway.list_buckets.side_effect = AwsPermissionError("aws.permission.denied", "denied")
    clock = Mock()
    clock.now.side_effect = lambda: datetime.now(UTC)
    dashboard = DashboardService(
        s3._profiles, s3._sessions, logs, clock, Mock(), Mock(), Mock(), gateway
    )
    result = dashboard.check_permissions(plan.profile_id)
    rows = {p.key: p for f in result.features if f.feature == "s3" for p in f.permissions}
    assert rows["buckets"].state is PermissionState.DENIED
    for key in ("put", "delete", "list"):
        assert rows[key].state is PermissionState.ALLOWED
    assert "reports/" in rows["list"].explanation
    assert gateway.put_file.call_count + gateway.multipart_file.call_count == 1
    assert gateway.delete_object.call_count == 1
    assert gateway.rename_object.call_count == 1


def test_s3_permissions_retain_success_older_than_one_day(history):
    from tests.unit.application.test_dashboard_service import build, row

    from aws_connect.application.dashboard_service import PermissionState

    logs, _, _ = history
    logs.record(
        replace(
            event(),
            occurred_at=datetime.now(UTC) - timedelta(days=2),
            feature="s3",
            aws_service="s3",
            aws_action="PutObject",
            metadata_json=json.dumps({"profile_id": 1, "region": "example-region"}),
        )
    )
    dashboard, *_ = build()
    dashboard._activity_logs = logs
    assert row(dashboard.check_permissions(1), "s3", "put").state is PermissionState.ALLOWED


def test_ec2_previous_day_success_survives_service_recreation_and_scopes(history):
    from tests.unit.application.test_ec2_service import build_service

    from aws_connect.application.operations import OperationState
    from aws_connect.application.ssm_session import ManagedSsmSession
    from aws_connect.domain.errors import ApplicationError

    logs, store, _ = history
    ec2, *_ = build_service()
    ec2._activity_logs = logs
    ec2._managed_runner = Mock()
    ec2._managed_runner.start.return_value = ManagedSsmSession(
        "op", 123, "session", OperationState.RUNNING
    )
    yesterday = datetime.now(UTC) - timedelta(days=2)
    logs._clock.now.side_effect = None
    logs._clock.now.return_value = yesterday
    ec2.connect_external("i-online", region="us-east-1")
    restored = ExecutionLogService(
        logs._settings,
        logs._reader,
        logs._writer,
        logs._clock,
        repository=store,
        sanitizer=MaskedExecutionLogSanitizer(),
    )
    assert restored.recent_ec2_connections(1, "us-east-1") == {"i-online": yesterday}
    assert restored.recent_ec2_connections(2, "us-east-1") == {}
    assert restored.recent_ec2_connections(1, "ap-northeast-2") == {}
    ec2._managed_runner.start.side_effect = ApplicationError("plugin.launch.failed", "fixture")
    with pytest.raises(ApplicationError):
        ec2.connect_external("i-online", region="us-east-1")
    assert restored.recent_ec2_connections(1, "us-east-1") == {"i-online": yesterday}


def test_ec2_terminal_completion_logs_success_and_real_failure_separately(history):
    from tests.unit.application.test_ec2_service import build_service

    from aws_connect.application.operations import OperationState
    from aws_connect.application.ssm_session import ManagedSsmSession
    from aws_connect.domain.errors import PluginExecutionError

    logs, _, _ = history
    ec2, *_ = build_service()
    ec2._activity_logs = logs
    runner = Mock()
    ec2._managed_runner = runner
    for operation, state, code in (
        ("normal", OperationState.SUCCEEDED, 0),
        ("broken", OperationState.FAILED, 1),
    ):
        runner.start.return_value = ManagedSsmSession(
            operation, 123, "session", OperationState.RUNNING
        )
        ec2.connect_external("i-online")
        runner.status.return_value = ManagedSsmSession(
            operation,
            123,
            "session",
            state,
            code,
            PluginExecutionError("plugin.exit.nonzero", "fixture") if code else None,
        )
        ec2.reap_external_sessions()
        ec2.reap_external_sessions()  # Terminal outcome must be recorded exactly once.
    events = [e for e in logs.list_recent(30) if e.action == "session_end"]
    assert len(events) == 2
    assert {(e.result, e.level) for e in events} == {
        (ExecutionResult.SUCCESS, ExecutionLevel.INFO),
        (ExecutionResult.FAILURE, ExecutionLevel.ERROR),
    }
