import logging
import zipfile
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.domain.errors import ConfigurationError
from aws_connect.infrastructure.diagnostic_logs import MaskedDiagnosticLogExporter
from aws_connect.infrastructure.logging_setup import RotatingLogConfigurator

TEST_ACCESS_KEY = "AKIA" + "ABCDEFGH" + "IJKLMNOP"


def test_rotating_logger_writes_to_configured_unicode_path_and_masks(tmp_path: Path) -> None:
    directory = tmp_path / "한글 로그 경로"
    configurator = RotatingLogConfigurator(max_bytes=80, backups=2)
    path = configurator.apply(AppSettings(directory, LogLevel.DEBUG))
    logger = logging.getLogger("aws_connect.test")

    for index in range(10):
        logger.info("rotation event %s with enough text", index)
    logger.info("access_key=%s secret_key=literal-secret", TEST_ACCESS_KEY)
    for handler in logging.getLogger("aws_connect").handlers:
        handler.flush()

    combined = "".join(
        item.read_text(encoding="utf-8") for item in directory.glob("aws-connect.log*")
    )
    assert path.exists()
    assert TEST_ACCESS_KEY not in combined
    assert "literal-secret" not in combined
    assert "***REDACTED***" in combined
    assert list(directory.glob("aws-connect.log.*"))


def test_diagnostic_export_remasks_historical_logs_and_is_atomic(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "aws-connect.log").write_text(
        'TokenValue="raw-token" StreamUrl=https://secret.example\n', encoding="utf-8"
    )
    destination = tmp_path / "export" / "diagnostics.zip"

    count = MaskedDiagnosticLogExporter().export(logs, destination)

    assert count == 1
    assert not destination.with_suffix(".zip.tmp").exists()
    with zipfile.ZipFile(destination) as archive:
        exported = archive.read("aws-connect.log").decode()
        assert "raw-token" not in exported
        assert "secret.example" not in exported
        assert "***REDACTED***" in exported


def test_diagnostic_export_rejects_non_zip_and_destination_inside_logs(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    exporter = MaskedDiagnosticLogExporter()

    with pytest.raises(ConfigurationError, match="diagnostics.destination.zip_required"):
        exporter.export(logs, tmp_path / "diagnostics.txt")
    with pytest.raises(ConfigurationError, match="diagnostics.destination.inside_logs"):
        exporter.export(logs, logs / "diagnostics.zip")


def test_logging_and_diagnostic_artifacts_are_restricted_after_creation(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    rotated = logs / "aws-connect.log.1"
    rotated.write_text("historical", encoding="utf-8")
    file_access = Mock()

    log_path = RotatingLogConfigurator(file_access).apply(AppSettings(logs, LogLevel.INFO))
    destination = tmp_path / "export" / "diagnostics.zip"
    MaskedDiagnosticLogExporter(file_access).export(logs, destination)

    assert file_access.restrict.call_args_list[:3] == [
        call(logs),
        call(rotated),
        call(log_path),
    ]
    assert call(destination.with_suffix(".zip.tmp")) in file_access.restrict.call_args_list
    assert file_access.restrict.call_args_list[-1] == call(destination)


def test_acl_failure_is_not_hidden_by_log_or_diagnostic_boundaries(tmp_path: Path) -> None:
    file_access = Mock()
    failure = ConfigurationError("private_file_access.restrict_failed", "PermissionError")
    file_access.restrict.side_effect = failure

    with pytest.raises(ConfigurationError) as logging_failure:
        RotatingLogConfigurator(file_access).apply(AppSettings(tmp_path / "logs", LogLevel.INFO))
    assert logging_failure.value is failure

    logs = tmp_path / "existing-logs"
    logs.mkdir()
    with pytest.raises(ConfigurationError) as export_failure:
        MaskedDiagnosticLogExporter(file_access).export(
            logs,
            tmp_path / "export" / "diagnostics.zip",
        )
    assert export_failure.value is failure
