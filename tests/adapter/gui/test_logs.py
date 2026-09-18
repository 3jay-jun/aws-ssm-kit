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
    assert page.entries.columnCount() == 6
    assert [page.entries.horizontalHeaderItem(index).text() for index in range(6)] == [
        "시간",
        "기능",
        "작업",
        "대상",
        "결과",
        "내용",
    ]
    assert page.entries.cellWidget(0, 1).findChild(QLabel).pixmap() is not None
    assert page.entries.item(0, 3).text() == "logs"
    assert page.entries.rowHeight(0) >= 40
    assert page.detail_message.text() == "Access denied after safe retry"
    assert page._metadata_labels["Correlation ID"].text() == "correlation-1"
    assert page._metadata_labels["Operation ID"].text() == "operation-1"
    assert page._metadata_labels["AWS 서비스"].text() == "s3"
    assert page._metadata_labels["AWS Action"].text() == "ListObjectsV2"
    assert page._metadata_labels["AWS Request ID"].text() == "-"
    assert page.findChild(QLabel, "page_title").text() == "실행 로그"
    assert "프로그램 동작과 AWS 요청 이력" in page.findChild(QLabel, "page_subtitle").text()
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
    assert "메시지: second.succeeded" in copied
    assert "재시도 가능: 아니요" in copied
    assert "first.failed" not in copied
    assert "마스킹된" in notices[-1]


def test_log_filters_are_combined_in_activity_log_query(tmp_path: Path) -> None:
    _app()
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    activity = Mock()
    activity.recent.return_value = []
    page = LogsSettingsPage(settings, activity, Mock(), ImmediateTaskRunner())  # type: ignore[arg-type]

    page.period_filter.setCurrentText("최근 7일")
    page.level_filter.setCurrentText("ERROR")
    page.feature_filter.setCurrentText("EC2")
    page.search.setText("StartSession")
    page.errors_only.setChecked(True)

    query = activity.recent.call_args.kwargs["query"]
    assert query.period_days == 7
    assert query.levels == ("ERROR",)
    assert query.features == ("ec2",)
    assert query.search == "StartSession"
    assert query.errors_only is True


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


def test_styled_log_labels_badges_fit_and_details_keep_columns(tmp_path: Path) -> None:
    from aws_connect.presentation.gui.styles import APP_STYLE

    app = _app()
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    activity = Mock()
    activity.recent.return_value = [
        ManagedLogEntry(datetime.now(UTC), feature, "-", result, "완료", level="INFO")
        for feature in ("ec2", "rds", "s3", "secrets", "auth", "dashboard", "program")
        for result in ("SUCCESS", "WARNING", "FAILURE")
    ]
    page = LogsSettingsPage(settings, activity, Mock(), ImmediateTaskRunner())  # type: ignore[arg-type]
    page.setStyleSheet(APP_STYLE)
    page.resize(1204, 894)
    page.refresh()
    page.show()
    app.processEvents()
    for caption, control in (
        ("기간", page.period_filter),
        ("레벨", page.level_filter),
        ("기능", page.feature_filter),
        ("검색", page.search),
    ):
        label = next(label for label in page.findChildren(QLabel) if label.buddy() is control)
        assert label.text() == caption and label.isVisible()
        assert label.geometry().bottom() < control.geometry().top()
    for width in (1204, 804):
        page.resize(width, 894)
        app.processEvents()
        for row in range(page.entries.rowCount()):
            for column in (1, 4):
                widget = page.entries.cellWidget(row, column)
                label = widget.findChild(QLabel, "icon_text_label")
                assert label.width() >= label.sizeHint().width()
                assert widget.width() >= widget.minimumSizeHint().width()
            page.entries.selectRow(row)
            app.processEvents()
            badge = page.entries.cellWidget(row, 4).findChild(QLabel, "icon_text_label")
            assert badge.styleSheet() == page._detail_labels["결과"].styleSheet()
    positions = [label.geometry().x() for label in page._metadata_labels.values()]
    assert len(set(positions)) == 2
    page._show_detail(None)
    app.processEvents()
    assert all(label.text() == "-" for label in page._metadata_labels.values())
    page.close()


def test_compact_detail_prioritizes_rows_and_preserves_copy_when_collapsed(tmp_path: Path) -> None:
    from dataclasses import replace

    from aws_connect.domain.execution_log import ErrorCategory
    from aws_connect.presentation.gui.styles import APP_STYLE

    app = _app()
    settings = Mock()
    settings.get.return_value = AppSettings(tmp_path, LogLevel.INFO)
    activity = Mock()
    success = ManagedLogEntry(
        datetime.now(UTC),
        "rds",
        "(DEV) example",
        "SUCCESS",
        "터널이 종료되었습니다.",
        level="INFO",
        operation="stop",
        operation_id="operation-example",
        correlation_id="correlation-example",
    )
    failure = replace(
        success,
        result="FAILURE",
        level="ERROR",
        error_code="AccessDenied",
        error_category=ErrorCategory.PERMISSION,
        required_permission="ssm:StartSession",
    )
    activity.recent.return_value = [success, failure] + [success] * 18
    page = LogsSettingsPage(settings, activity, Mock(), ImmediateTaskRunner())  # type: ignore[arg-type]
    page.setStyleSheet(APP_STYLE)
    page.resize(1204, 780)  # Content area below the application header.
    page.refresh()
    page.show()
    app.processEvents()
    assert page.entries.viewport().height() // page.entries.rowHeight(0) >= 7
    assert page.detail_errors.isHidden()
    expanded_height = page.entries.height()
    page.detail_toggle.click()
    app.processEvents()
    assert page.detail_body.isHidden()
    assert page.entries.height() > expanded_height + 100
    assert page.entries.viewport().height() // page.entries.rowHeight(0) >= 10
    page.entries.selectRow(1)
    page.copy_detail_button.click()
    assert "AccessDenied" in QApplication.clipboard().text()
    assert "Operation ID: operation-example" in QApplication.clipboard().text()
    assert page.detail_body.isHidden()
    page.detail_toggle.click()
    app.processEvents()
    assert page.detail_errors.isVisible()
    assert "필요 권한: ssm:StartSession" in page.detail_errors.text()
    page._show_detail(replace(failure, result="SUCCESS"))
    assert page.detail_errors.isHidden()
    page._show_detail(replace(success, result="WARNING"))
    assert page.detail_errors.isHidden()
    page._show_detail(replace(failure, result="WARNING"))
    assert not page.detail_errors.isHidden()
    page._show_detail(replace(failure, target="long-target-" * 100, masked_detail="message " * 500))
    app.processEvents()
    assert page.entries.viewport().height() // page.entries.rowHeight(0) >= 7
    assert page.detail_body.verticalScrollBar().maximum() > 0
    page.detail_toggle.click()
    page.refresh()
    assert page.detail_body.isHidden()
    page._show_detail(None)
    assert page.detail_errors.isHidden()
    assert not page.copy_detail_button.isEnabled()
    page.close()
