from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_connect.application.activity_log_service import (
    ActivityEvent,
    ActivityLogQuery,
    ActivityLogService,
    ManagedLogEntry,
    filter_activity_logs,
)
from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.domain.errors import ConfigurationError


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 11, 1, 2, 3, tzinfo=UTC)


def test_activity_service_records_only_typed_event_metadata(tmp_path: Path) -> None:
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    reader = Mock()
    writer = Mock()
    service = ActivityLogService(settings, reader, writer, FixedClock())

    service.record(
        feature="cli.settings",
        target="update",
        result="succeeded",
        message_code="cli.command.succeeded",
        correlation_id="correlation-1",
        operation_id="operation-1",
        aws_service="s3",
        aws_action="PutObject",
        retryable=True,
        masked_detail="request timed out",
    )

    writer.write.assert_called_once_with(
        ActivityEvent(
            datetime(2026, 9, 11, 1, 2, 3, tzinfo=UTC),
            "cli.settings",
            "update",
            "succeeded",
            "cli.command.succeeded",
            correlation_id="correlation-1",
            operation_id="operation-1",
            aws_service="s3",
            aws_action="PutObject",
            retryable=True,
            masked_detail="request timed out",
        )
    )


def test_activity_service_rejects_non_allowlisted_raw_diagnostics(tmp_path: Path) -> None:
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    service = ActivityLogService(settings, Mock(), Mock(), FixedClock())

    with pytest.raises(TypeError):
        service.record(  # type: ignore[call-arg]
            feature="gui",
            target="logs",
            result="failed",
            message_code="gui.failed",
            traceback="raw traceback",
        )


def test_recent_uses_current_settings_and_bounds_limit(tmp_path: Path) -> None:
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path / "logs", LogLevel.INFO)
    entry = ManagedLogEntry(
        datetime(2026, 9, 11, tzinfo=UTC), "gui", "logs", "notice", "gui.notice"
    )
    reader = Mock()
    reader.read_recent.return_value = [entry]
    service = ActivityLogService(settings, reader, Mock(), FixedClock())

    assert service.recent(10) == [entry]
    reader.read_recent.assert_called_once_with(tmp_path / "logs", 10)
    for value in (0, 501):
        with pytest.raises(ConfigurationError, match="logs.limit.invalid"):
            service.recent(value)


def test_activity_log_query_filters_period_level_feature_search_and_errors() -> None:
    entries = [
        ManagedLogEntry(
            datetime(2026, 9, 11, tzinfo=UTC),
            "ec2",
            "instance-1",
            "failed",
            "ec2.start.failed",
            operation="SSM 세션 시작",
            level="ERROR",
            operation_id="op-1",
            correlation_id="corr-1",
            aws_action="StartSession",
            masked_detail="EC2 instance failed",
        ),
        ManagedLogEntry(
            datetime(2026, 9, 1, tzinfo=UTC),
            "s3",
            "bucket/key",
            "succeeded",
            "s3.upload.succeeded",
            operation="파일 업로드",
            level="INFO",
            aws_action="PutObject",
        ),
    ]

    result = filter_activity_logs(
        entries,
        ActivityLogQuery(
            period_days=7,
            levels=("ERROR",),
            features=("ec2",),
            search="startsession",
            errors_only=True,
        ),
        now=datetime(2026, 9, 11, 12, tzinfo=UTC),
    )

    assert result == [entries[0]]


def test_recent_connections_exclude_failures_other_scopes_and_legacy_notices(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    from datetime import timedelta

    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    reader = Mock()
    entry = ManagedLogEntry(
        FixedClock().now(),
        "ec2",
        "i-test",
        "succeeded",
        "ec2.connection.succeeded",
        profile_id=1,
        region="us-east-1",
    )
    reader.read_recent.return_value = [
        replace(entry, occurred_at=entry.occurred_at - timedelta(days=2)),
        entry,
        replace(entry, target="i-failed", result="failed"),
        replace(entry, target="i-other-profile", profile_id=2),
        replace(entry, target="i-other-region", region="us-west-2"),
        replace(entry, target="i-legacy", profile_id=None),
        replace(entry, target="i-notice", message_code="gui.notice"),
        replace(entry, target="i-other-feature", feature="rds"),
    ]
    service = ActivityLogService(settings, reader, Mock(), FixedClock())
    assert service.recent_ec2_connections(1, "us-east-1") == {"i-test": entry.occurred_at}
