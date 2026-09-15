import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from aws_connect.application.activity_log_service import ManagedLogEntry
from aws_connect.application.legacy_import import LegacyImportPreview, LegacyImportResult
from aws_connect.application.settings_service import DiagnosticExport
from aws_connect.bootstrap import ApplicationServices
from aws_connect.cli_main import main
from aws_connect.domain.app_settings import AppSettings, LogLevel


def services(*, activity_logs: Mock | None = None) -> ApplicationServices:
    return ApplicationServices(
        Mock(),
        Mock(),
        Mock(),
        Mock(),
        settings=Mock(),
        diagnostic_logs=Mock(),
        legacy_import=Mock(),
        activity_logs=activity_logs,
    )


def test_settings_and_diagnostic_export_use_shared_services(capsys, tmp_path: Path) -> None:
    app = services()
    app.settings.update.return_value = AppSettings(tmp_path / "한글 로그", LogLevel.ERROR)
    assert (
        main(
            [
                "settings",
                "update",
                "--log-directory",
                str(tmp_path / "한글 로그"),
                "--log-level",
                "ERROR",
                "--output",
                "json",
            ],
            services=app,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["log_level"] == "ERROR"
    assert app.settings.update.call_args.args[0].log_directory == tmp_path / "한글 로그"

    destination = tmp_path / "diagnostics.zip"
    app.diagnostic_logs.export.return_value = DiagnosticExport(destination, 2)
    assert main(["logs", "export", str(destination), "--output", "json"], services=app) == 0
    assert json.loads(capsys.readouterr().out)["files_exported"] == 2


def test_logs_show_uses_masked_application_contract_and_records_safe_command_event(
    capsys,
) -> None:
    activity_logs = Mock()
    app = services(activity_logs=activity_logs)
    activity_logs.recent.return_value = [
        ManagedLogEntry(
            datetime(2026, 9, 11, tzinfo=UTC),
            "gui",
            "logs",
            "notice",
            "gui.notice",
        )
    ]

    assert main(["logs", "show", "--limit", "1", "--output", "json"], services=app) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["log_entries"][0]["message_code"] == "gui.notice"
    activity_logs.recent.assert_called_once_with(1)
    recorded = activity_logs.record.call_args.kwargs
    assert recorded == {
        "feature": "cli.logs",
        "target": "show",
        "result": "succeeded",
        "message_code": "cli.command.succeeded",
        "correlation_id": None,
    }


def test_activity_log_failure_is_visible_without_replacing_command_result(capsys) -> None:
    activity_logs = Mock()
    activity_logs.record.side_effect = OSError("private path detail")
    app = services(activity_logs=activity_logs)
    app.settings.get.return_value = AppSettings(Path("logs"), LogLevel.INFO)

    assert main(["settings", "show", "--output", "json"], services=app) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["log_level"] == "INFO"
    assert captured.err == "logs.write.failed\n"
    assert "private path detail" not in captured.err


def test_legacy_preview_then_explicit_import_have_no_secret_fields(capsys, tmp_path: Path) -> None:
    app = services()
    source = tmp_path / "aws_info.ini"
    app.legacy_import.preview.return_value = LegacyImportPreview(
        "legacy",
        "ap-northeast-2",
        "123456789012",
        "developer",
        "db",
        "db.internal",
        3306,
        13306,
    )
    assert (
        main(
            [
                "legacy",
                "preview",
                str(source),
                "--profile-name",
                "legacy",
                "--output",
                "json",
            ],
            services=app,
        )
        == 0
    )
    preview = capsys.readouterr().out
    assert "access_key" not in preview
    assert "secret_key" not in preview

    app.legacy_import.apply.return_value = LegacyImportResult(1, "legacy", 2, True)
    assert (
        main(
            [
                "legacy",
                "import",
                str(source),
                "--profile-name",
                "legacy",
                "--confirm",
                "--output",
                "json",
            ],
            services=app,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["source_preserved"] is True
    assert app.legacy_import.apply.call_args.args[0].confirmed is True
