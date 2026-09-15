from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFrame, QLabel

from aws_connect.application.activity_log_service import ManagedLogEntry
from aws_connect.application.settings_service import DiagnosticExport, UpdateSettingsRequest
from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.presentation.gui.logs import LogsSettingsPage


class ImmediateTaskRunner:
    def __init__(self) -> None:
        self.submissions = 0

    def submit(
        self,
        operation: Callable[[], object],
        on_success: Callable[[Any], None],
        on_error: Callable[[ApplicationError], None],
    ) -> None:
        self.submissions += 1
        try:
            on_success(operation())
        except ApplicationError as error:
            on_error(error)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_logs_page_is_authentication_free_and_renders_masked_entries(tmp_path: Path) -> None:
    _app()
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path / "로그", LogLevel.WARNING)
    activity = Mock()
    activity.recent.return_value = [
        ManagedLogEntry(
            datetime(2026, 9, 11, 1, 2, 3, tzinfo=UTC),
            "gui",
            "logs",
            "notice",
            "gui.notice",
            correlation_id="correlation-1",
            operation_id="operation-1",
            aws_service="s3",
            aws_action="ListObjectsV2",
            retryable=True,
            masked_detail="Access denied after safe retry",
        )
    ]
    page = LogsSettingsPage(settings, activity, Mock(), ImmediateTaskRunner())  # type: ignore[arg-type]

    page.refresh()

    assert page.log_directory.text() == str(tmp_path / "로그")
    assert page.log_level.currentText() == "WARNING"
    assert page.entries.rowCount() == 1
    assert page.entries.columnCount() == 5
    assert [page.entries.horizontalHeaderItem(index).text() for index in range(5)] == [
        "No",
        "시간",
        "대상(탭)",
        "내용",
        "결과",
    ]
    assert page.entries.item(0, 0).text() == "1"
    assert page.entries.item(0, 2).text() == "gui"
    assert page.entries.rowHeight(0) >= 48
    assert "메시지 코드: gui.notice" in page.detail_text.text()
    assert "Correlation ID: correlation-1" in page.detail_text.text()
    assert "Operation ID: operation-1" in page.detail_text.text()
    assert "AWS 서비스: s3" in page.detail_text.text()
    assert "AWS Action: ListObjectsV2" in page.detail_text.text()
    assert "재시도 가능: 예" in page.detail_text.text()
    assert "상세 원인: Access denied after safe retry" in page.detail_text.text()
    assert page.findChild(QLabel, "page_title").text() == "실행 로그"
    assert page.findChild(QFrame, "managed_log_card") is not None
    assert page.open_button.text() == "로그 폴더 열기 ↗"
    assert page.settings_panel.isHidden()
    page.settings_toggle.setChecked(True)
    assert not page.settings_panel.isHidden()


def test_detail_selection_and_copy_use_only_masked_entry_projection(tmp_path: Path) -> None:
    app = _app()
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    first = ManagedLogEntry(
        datetime(2026, 9, 11, tzinfo=UTC),
        "gui",
        "first",
        "failed",
        "first.failed",
        masked_detail="***REDACTED***",
    )
    second = ManagedLogEntry(
        datetime(2026, 9, 12, tzinfo=UTC),
        "gui",
        "second",
        "succeeded",
        "second.succeeded",
        retryable=False,
    )
    activity = Mock()
    activity.recent.return_value = [first, second]
    page = LogsSettingsPage(settings, activity, Mock(), ImmediateTaskRunner())  # type: ignore[arg-type]
    notices: list[str] = []
    page.notice_raised.connect(notices.append)

    page.refresh()
    page.entries.selectRow(1)
    app.processEvents()
    page.copy_detail_button.click()

    copied = QApplication.clipboard().text()
    assert "메시지 코드: second.succeeded" in copied
    assert "재시도 가능: 아니요" in copied
    assert "first.failed" not in copied
    assert "마스킹된" in notices[-1]


def test_update_reset_and_explicit_export_use_background_runner_contract(tmp_path: Path) -> None:
    _app()
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path / "initial", LogLevel.INFO)
    settings.update.return_value = AppSettings(tmp_path / "changed", LogLevel.ERROR)
    settings.reset.return_value = AppSettings(tmp_path / "default", LogLevel.INFO)
    activity = Mock()
    activity.recent.return_value = []
    diagnostics = Mock()
    destination = tmp_path / "explicit diagnostics.zip"
    diagnostics.export.return_value = DiagnosticExport(destination, 2)
    runner = ImmediateTaskRunner()
    page = LogsSettingsPage(settings, activity, diagnostics, runner)  # type: ignore[arg-type]
    notices: list[str] = []
    page.notice_raised.connect(notices.append)
    page.log_directory.setText(str(tmp_path / "changed"))
    page.log_level.setCurrentText("ERROR")

    page.update_settings()
    page.reset_settings()
    page.export_diagnostics(destination)

    request = settings.update.call_args.args[0]
    assert request == UpdateSettingsRequest(str(tmp_path / "changed"), "ERROR")
    settings.reset.assert_called_once_with()
    diagnostics.export.assert_called_once_with(destination)
    assert runner.submissions >= 5  # update/reset trigger a separate masked refresh
    assert any("2개" in notice for notice in notices)


def test_log_read_and_export_errors_are_typed_and_page_remains_alive(tmp_path: Path) -> None:
    _app()
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    activity = Mock()
    activity.recent.side_effect = ConfigurationError("logs.read.failed", "PermissionError")
    diagnostics = Mock()
    diagnostics.export.side_effect = ConfigurationError("diagnostics.export.failed", "OSError")
    page = LogsSettingsPage(settings, activity, diagnostics, ImmediateTaskRunner())  # type: ignore[arg-type]
    errors: list[ApplicationError] = []
    page.error_raised.connect(errors.append)

    page.refresh()
    page.export_diagnostics(tmp_path / "diagnostics.zip")

    assert [error.message_code for error in errors] == [
        "logs.read.failed",
        "diagnostics.export.failed",
    ]
    assert not page.isHidden() or not page.isVisible()


def test_failed_settings_write_restores_visible_current_values(tmp_path: Path) -> None:
    _app()
    settings = Mock()
    current = AppSettings(tmp_path / "current", LogLevel.INFO)
    settings.get.return_value = current
    settings.update.side_effect = ConfigurationError("settings.persistence.failed", "OSError")
    activity = Mock()
    activity.recent.return_value = []
    page = LogsSettingsPage(settings, activity, Mock(), ImmediateTaskRunner())  # type: ignore[arg-type]
    errors: list[ApplicationError] = []
    page.error_raised.connect(errors.append)
    page.log_directory.setText(str(tmp_path / "uncommitted"))
    page.log_level.setCurrentText("ERROR")

    page.update_settings()

    assert errors[0].message_code == "settings.persistence.failed"
    assert page.log_directory.text() == str(current.log_directory)
    assert page.log_level.currentText() == "INFO"


def test_open_log_folder_reports_os_rejection(monkeypatch, tmp_path: Path) -> None:
    _app()
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    activity = Mock()
    activity.recent.return_value = []
    page = LogsSettingsPage(settings, activity, Mock(), ImmediateTaskRunner())  # type: ignore[arg-type]
    errors: list[ApplicationError] = []
    page.error_raised.connect(errors.append)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: False)

    page.open_log_folder()

    assert errors[0].message_code == "logs.directory.open_failed"
