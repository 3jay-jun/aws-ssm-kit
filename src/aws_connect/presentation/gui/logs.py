"""Settings and masked managed-log GUI page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from aws_connect.application.activity_log_service import ActivityLogService, ManagedLogEntry
from aws_connect.application.settings_service import (
    DiagnosticExport,
    DiagnosticLogService,
    SettingsService,
    UpdateSettingsRequest,
)
from aws_connect.domain.app_settings import AppSettings
from aws_connect.presentation.gui.icons import set_button_icon
from aws_connect.presentation.gui.table_selection import use_first_column_selection_bar
from aws_connect.presentation.gui.tasks import GuiTaskRunner


class LogsSettingsPage(QWidget):
    """Authentication-free UI for local settings, logs, and diagnostics."""

    error_raised = Signal(object)
    notice_raised = Signal(str)

    def __init__(
        self,
        settings: SettingsService,
        activity_logs: ActivityLogService,
        diagnostic_logs: DiagnosticLogService,
        runner: GuiTaskRunner,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._activity_logs = activity_logs
        self._diagnostic_logs = diagnostic_logs
        self._runner = runner
        self._entries: list[ManagedLogEntry] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 40)
        layout.setSpacing(16)
        page_head = QHBoxLayout()
        heading_copy = QVBoxLayout()
        title = QLabel("실행 로그")
        title.setObjectName("page_title")
        subtitle = QLabel("민감한 인증정보는 마스킹된 상태로 기록됩니다.")
        subtitle.setObjectName("page_subtitle")
        heading_copy.addWidget(title)
        heading_copy.addWidget(subtitle)
        page_head.addLayout(heading_copy)
        page_head.addStretch()
        self.open_button = QPushButton("로그 폴더 열기 ↗")
        self.open_button.setObjectName("logs_open_folder")
        self.open_button.clicked.connect(self.open_log_folder)
        page_head.addWidget(self.open_button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(page_head)
        layout.addSpacing(8)

        log_card = QFrame()
        log_card.setObjectName("managed_log_card")
        log_card.setProperty("role", "card")
        card_layout = QVBoxLayout(log_card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_actions = QHBoxLayout()
        card_actions.addStretch()
        self.refresh_button = QPushButton("새로고침")
        self.refresh_button.setObjectName("logs_refresh")
        set_button_icon(self.refresh_button, "common-refresh.svg")
        self.export_button = QPushButton("진단 ZIP 내보내기")
        self.export_button.setObjectName("logs_export")
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self._choose_export_destination)
        card_actions.addWidget(self.refresh_button)
        card_actions.addWidget(self.export_button)
        self.refresh_button.hide()
        self.export_button.hide()
        card_layout.addLayout(card_actions)

        self.entries = QTableWidget(0, 5)
        self.entries.setObjectName("managed_log_entries")
        self.entries.setHorizontalHeaderLabels(("No", "시간", "대상(탭)", "내용", "결과"))
        self.entries.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.entries.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.entries.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.entries.currentCellChanged.connect(self._selection_changed)
        self.entries.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.entries.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.entries.verticalHeader().setVisible(False)
        self.entries.verticalHeader().setDefaultSectionSize(48)
        self.entries.verticalHeader().setMinimumSectionSize(48)
        use_first_column_selection_bar(self.entries)
        card_layout.addWidget(self.entries, 1)

        details = QFrame()
        details.setObjectName("managed_log_detail")
        detail_layout = QVBoxLayout(details)
        detail_header = QHBoxLayout()
        detail_title = QLabel("로그 상세")
        detail_title.setObjectName("managed_log_detail_title")
        self.copy_detail_button = QPushButton("상세 복사")
        self.copy_detail_button.setObjectName("managed_log_detail_copy")
        set_button_icon(self.copy_detail_button, "common-clipboard.svg")
        self.copy_detail_button.setEnabled(False)
        self.copy_detail_button.clicked.connect(self.copy_selected_detail)
        detail_header.addWidget(detail_title)
        detail_header.addStretch()
        detail_header.addWidget(self.copy_detail_button)
        detail_layout.addLayout(detail_header)
        self.detail_text = QLabel("로그를 선택하면 안전하게 마스킹된 상세 정보가 표시됩니다.")
        self.detail_text.setObjectName("managed_log_detail_text")
        self.detail_text.setWordWrap(True)
        self.detail_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_text)
        card_layout.addWidget(details)
        notice = QLabel(
            "Access Key, Secret Key, Session Token, MFA 코드와 Secret 원문은 표시하거나 "
            "내보내지 않습니다. 상세 복사와 진단 ZIP에도 같은 마스킹 정책을 적용합니다."
        )
        notice.setObjectName("managed_log_notice")
        notice.setWordWrap(True)
        notice.hide()
        card_layout.addWidget(notice)
        layout.addWidget(log_card, 1)

        self.settings_toggle = QToolButton()
        self.settings_toggle.setObjectName("log_settings_toggle")
        self.settings_toggle.setText("▸ 로그 설정")
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.toggled.connect(self._toggle_settings)
        self.settings_toggle.hide()
        layout.addWidget(self.settings_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.settings_panel = QFrame()
        self.settings_panel.setObjectName("log_settings_panel")
        settings_layout = QVBoxLayout(self.settings_panel)
        form = QFormLayout()
        self.log_directory = QLineEdit()
        self.log_directory.setObjectName("log_directory")
        self.log_level = QComboBox()
        self.log_level.setObjectName("log_level")
        self.log_level.addItems(("DEBUG", "INFO", "WARNING", "ERROR"))
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.log_directory, 1)
        self.browse_button = QPushButton("찾아보기")
        self.browse_button.clicked.connect(self._browse)
        directory_row.addWidget(self.browse_button)
        form.addRow("로그 폴더", directory_row)
        form.addRow("로그 수준", self.log_level)
        settings_layout.addLayout(form)

        actions = QHBoxLayout()
        self.save_button = QPushButton("설정 저장")
        self.reset_button = QPushButton("기본값 복원")
        set_button_icon(self.save_button, "common-save.svg")
        self.save_button.clicked.connect(self.update_settings)
        self.reset_button.clicked.connect(self.reset_settings)
        for button in (self.save_button, self.reset_button):
            actions.addWidget(button)
        actions.addStretch()
        settings_layout.addLayout(actions)
        self.settings_panel.hide()
        layout.addWidget(self.settings_panel)

    def _toggle_settings(self, expanded: bool) -> None:
        self.settings_toggle.setText("▾ 로그 설정" if expanded else "▸ 로그 설정")
        self.settings_panel.setVisible(expanded)

    def refresh(self) -> None:
        self._runner.submit(
            lambda: (self._settings.get(), self._activity_logs.recent()),
            self._loaded,
            self.error_raised.emit,
        )

    def _loaded(self, value: Any) -> None:
        settings, entries = value
        self._apply_settings(settings)
        self._apply_entries(list(entries))

    def _apply_settings(self, settings: AppSettings) -> None:
        self.log_directory.setText(str(settings.log_directory))
        self.log_level.setCurrentText(settings.log_level.value)

    def _apply_entries(self, entries: list[ManagedLogEntry]) -> None:
        self._entries = entries
        self.entries.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (
                str(row + 1),
                entry.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                entry.feature,
                entry.target,
                entry.result,
            )
            for column, value in enumerate(values):
                if column == 4:
                    status = QLabel(value)
                    status.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    status.setProperty(
                        "status",
                        "success" if value in {"succeeded", "success"} else "error",
                    )
                    self.entries.setCellWidget(row, column, status)
                else:
                    self.entries.setItem(row, column, QTableWidgetItem(value))
            self.entries.setRowHeight(row, 48)
        if entries:
            self.entries.selectRow(0)
            self._show_detail(entries[0])
        else:
            self._show_detail(None)

    def _selection_changed(
        self, current_row: int, _current_column: int, _previous_row: int, _previous_column: int
    ) -> None:
        entry = self._entries[current_row] if 0 <= current_row < len(self._entries) else None
        self._show_detail(entry)

    def _show_detail(self, entry: ManagedLogEntry | None) -> None:
        if entry is None:
            self.detail_text.setText("로그를 선택하면 안전하게 마스킹된 상세 정보가 표시됩니다.")
            self.copy_detail_button.setEnabled(False)
            return
        self.detail_text.setText(_detail_text(entry))
        self.copy_detail_button.setEnabled(True)

    def copy_selected_detail(self) -> None:
        row = self.entries.currentRow()
        if not (0 <= row < len(self._entries)):
            return
        # Rebuild from the already re-masked, bounded ManagedLogEntry instead of
        # copying arbitrary widget or raw log contents.
        QApplication.clipboard().setText(_detail_text(self._entries[row]))
        self.notice_raised.emit("마스킹된 로그 상세 정보를 복사했습니다.")

    def update_settings(self) -> None:
        request = UpdateSettingsRequest(
            self.log_directory.text().strip(), self.log_level.currentText()
        )
        self._runner.submit(
            lambda: self._settings.update(request),
            lambda settings: self._settings_changed(settings, "로그 설정을 저장했습니다."),
            self._settings_failed,
        )

    def reset_settings(self) -> None:
        self._runner.submit(
            self._settings.reset,
            lambda settings: self._settings_changed(
                settings, "로그 설정을 기본값으로 복원했습니다."
            ),
            self._settings_failed,
        )

    def _settings_changed(self, value: Any, notice: str) -> None:
        self._apply_settings(value)
        self.notice_raised.emit(notice)
        self.refresh()

    def _settings_failed(self, error: object) -> None:
        # The Application service has already restored the prior logger. Reload so
        # the controls cannot continue to display an unapplied candidate value.
        self.error_raised.emit(error)
        self.refresh()

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "로그 폴더 선택", self.log_directory.text()
        )
        if selected:
            self.log_directory.setText(selected)

    def open_log_folder(self) -> None:
        self._runner.submit(
            self._settings.get,
            self._open_directory,
            self.error_raised.emit,
        )

    def _open_directory(self, settings: AppSettings) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(settings.log_directory))):
            from aws_connect.domain.errors import ConfigurationError

            self.error_raised.emit(
                ConfigurationError(
                    message_code="logs.directory.open_failed",
                    technical_cause="The operating system rejected the log directory URL",
                )
            )

    def _choose_export_destination(self) -> None:
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self, "진단 ZIP 저장", "aws-connect-diagnostics.zip", "ZIP archive (*.zip)"
        )
        if destination:
            self.export_diagnostics(Path(destination))

    def export_diagnostics(self, destination: Path) -> None:
        self._runner.submit(
            lambda: self._diagnostic_logs.export(destination),
            self._exported,
            self.error_raised.emit,
        )

    def _exported(self, value: Any) -> None:
        result: DiagnosticExport = value
        self.notice_raised.emit(
            f"진단 ZIP을 저장했습니다. 마스킹된 로그 {result.files_exported}개를 포함합니다."
        )


def _detail_text(entry: ManagedLogEntry) -> str:
    retryable = "예" if entry.retryable is True else "아니요" if entry.retryable is False else "-"
    values = (
        ("메시지 코드", entry.message_code),
        ("Correlation ID", entry.correlation_id or "-"),
        ("Operation ID", entry.operation_id or "-"),
        ("AWS 서비스", entry.aws_service or "-"),
        ("AWS Action", entry.aws_action or "-"),
        ("재시도 가능", retryable),
        ("상세 원인", entry.masked_detail or "-"),
    )
    return "\n".join(f"{label}: {value}" for label, value in values)
