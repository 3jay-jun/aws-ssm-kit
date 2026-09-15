import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from aws_connect.application.activity_log_service import ActivityEvent
from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.infrastructure.logging_setup import RotatingLogConfigurator
from aws_connect.infrastructure.managed_logs import (
    MaskedManagedLogReader,
    StructuredActivityEventWriter,
)

TEST_ACCESS_KEY = "AKIA" + "ABCDEFGH" + "IJKLMNOP"


def _flush() -> None:
    for handler in logging.getLogger("aws_connect").handlers:
        handler.flush()


def test_writer_and_reader_remask_allowlisted_metadata(tmp_path: Path) -> None:
    RotatingLogConfigurator().apply(AppSettings(tmp_path, LogLevel.INFO))
    StructuredActivityEventWriter().write(
        ActivityEvent(
            datetime(2026, 9, 11, tzinfo=UTC),
            "gui\nsecret_key=raw-secret",
            TEST_ACCESS_KEY,
            "succeeded",
            "gui.notice",
            correlation_id="password=correlation-secret",
            operation_id="operation\nsecond-line",
            aws_service="secretsmanager",
            aws_action="GetSecretValue",
            retryable=False,
            masked_detail=f"secret_key=raw-detail {TEST_ACCESS_KEY} " + ("x" * 300),
        )
    )
    _flush()

    entries = MaskedManagedLogReader().read_recent(tmp_path, 20)

    assert len(entries) == 1
    serialized = repr(entries[0])
    assert TEST_ACCESS_KEY not in serialized
    assert "raw-secret" not in serialized
    assert "raw-detail" not in serialized
    assert "***REDACTED***" in serialized
    assert "\n" not in entries[0].feature
    assert "\n" not in (entries[0].operation_id or "")
    assert entries[0].aws_service == "secretsmanager"
    assert entries[0].aws_action == "GetSecretValue"
    assert entries[0].retryable is False
    assert entries[0].masked_detail is not None
    assert len(entries[0].masked_detail) <= 160


def test_reader_keeps_legacy_entries_and_ignores_unknown_or_invalid_detail_fields(
    tmp_path: Path,
) -> None:
    legacy = {
        "occurred_at": "2026-09-11T00:00:00+00:00",
        "feature": "gui",
        "target": "logs",
        "result": "failed",
        "message_code": "legacy.failure",
        "payload": "must not be accepted",
        "traceback": "must not be accepted",
        "retryable": "false",
    }
    (tmp_path / "aws-connect.log").write_text(
        "activity_event=" + __import__("json").dumps(legacy), encoding="utf-8"
    )

    entries = MaskedManagedLogReader().read_recent(tmp_path, 20)

    assert len(entries) == 1
    assert entries[0].message_code == "legacy.failure"
    assert entries[0].retryable is None
    assert entries[0].masked_detail is None
    assert not hasattr(entries[0], "payload")
    assert not hasattr(entries[0], "traceback")


def test_reader_handles_rotation_corruption_unknown_files_and_tail_bounds(tmp_path: Path) -> None:
    configurator = RotatingLogConfigurator(max_bytes=220, backups=3)
    configurator.apply(AppSettings(tmp_path, LogLevel.INFO))
    writer = StructuredActivityEventWriter()
    start = datetime(2026, 9, 11, tzinfo=UTC)
    for index in range(8):
        writer.write(
            ActivityEvent(
                start + timedelta(seconds=index),
                "cli.s3",
                "upload",
                "succeeded",
                f"event.{index}",
            )
        )
    _flush()
    (tmp_path / "aws-connect.log.unowned").write_text(
        'activity_event={"occurred_at":"2099-01-01T00:00:00+00:00"}', encoding="utf-8"
    )
    with (tmp_path / "aws-connect.log").open("ab") as stream:
        stream.write(b"\nactivity_event={broken json}\n")

    entries = MaskedManagedLogReader().read_recent(tmp_path, 5)

    assert len(entries) <= 5
    assert entries
    assert all(entry.feature == "cli.s3" for entry in entries)
    assert entries == sorted(entries, key=lambda item: item.occurred_at, reverse=True)


def test_reader_returns_empty_for_missing_or_non_directory_source(tmp_path: Path) -> None:
    reader = MaskedManagedLogReader()
    assert reader.read_recent(tmp_path / "missing", 20) == []
    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")
    assert reader.read_recent(file_path, 20) == []


def test_reader_skips_unreadable_managed_file_without_leaking_os_error(tmp_path: Path) -> None:
    log = tmp_path / "aws-connect.log"
    log.write_text("activity_event={}", encoding="utf-8")
    original_open = Path.open

    def deny_managed_file(path: Path, *args: object, **kwargs: object):
        if path == log:
            raise PermissionError("private platform detail")
        return original_open(path, *args, **kwargs)

    with patch.object(Path, "open", deny_managed_file):
        assert MaskedManagedLogReader().read_recent(tmp_path, 20) == []
