from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_connect.application.activity_log_service import (
    ActivityEvent,
    ActivityLogService,
    ManagedLogEntry,
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
            "correlation-1",
            "operation-1",
            "s3",
            "PutObject",
            True,
            "request timed out",
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
