"""Settings and masked managed-log GUI page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from aws_connect.application.activity_log_service import (
    ActivityLogQuery,
    ActivityLogService,
    ManagedLogEntry,
)
from aws_connect.application.settings_service import (
    DiagnosticExport,
    DiagnosticLogService,
    SettingsService,
    UpdateSettingsRequest,
)
from aws_connect.domain.app_settings import AppSettings
from aws_connect.presentation.gui.icons import gui_icon, icon_text, set_button_icon, status_badge
from aws_connect.presentation.gui.table_selection import use_first_column_selection_bar
from aws_connect.presentation.gui.tasks import GuiTaskRunner
from aws_connect.presentation.gui.view_models import (
    ActivityLogRowViewModel,
    build_activity_log_row_view_model,
)


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
        self._refresh_generation = 0
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 20, 34, 20)
        layout.setSpacing(16)
        page_head = QHBoxLayout()
        heading_copy = QVBoxLayout()
        title = QLabel("실행 로그")
        title.setObjectName("page_title")
        subtitle = QLabel(
            "프로그램 동작과 AWS 요청 이력을 확인할 수 있습니다. "
            "민감한 인증정보와 Secret 값은 마스킹되어 기록됩니다."
        )
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

        filters = QHBoxLayout()
        filters.setSpacing(14)
        self.period_filter = self._filter_combo("기간", ("오늘", "최근 7일", "전체"))
        self.level_filter = self._filter_combo(
            "레벨", ("전체", "DEBUG", "INFO", "WARNING", "ERROR")
        )
        self.feature_filter = self._filter_combo(
            "기능", ("전체", "EC2", "RDS", "S3", "Secrets", "인증", "대시보드", "프로그램")
        )
        self.search = QLineEdit()
        self.search.setObjectName("logs_search")
        self.search.setPlaceholderText("메시지, Operation ID, Correlation ID 검색")
        self.search.addAction(
            gui_icon("common-search.svg"), QLineEdit.ActionPosition.LeadingPosition
        )
        self.errors_only = QCheckBox("오류만 보기")
        self.errors_only.setObjectName("logs_errors_only")
        self.refresh_button = QPushButton("새로고침")
        self.refresh_button.setObjectName("logs_refresh")
        set_button_icon(self.refresh_button, "common-refresh.svg")
        self.refresh_button.clicked.connect(self.refresh)
        for filter_caption, control in (
            ("기간", self.period_filter),
            ("레벨", self.level_filter),
            ("기능", self.feature_filter),
            ("검색", self.search),
        ):
            block = QVBoxLayout()
            block.setSpacing(6)
            filter_label = QLabel(filter_caption)
            filter_label.setObjectName("field_label")
            filter_label.setBuddy(control)
            block.addWidget(filter_label)
            block.addWidget(control)
            filters.addLayout(block, 1 if control is self.search else 0)
        for combo in (self.period_filter, self.level_filter, self.feature_filter):
            combo.currentTextChanged.connect(self._filter_changed)
        self.search.textChanged.connect(self._filter_changed)
        self.errors_only.toggled.connect(self._filter_changed)
        filters.addWidget(self.errors_only, alignment=Qt.AlignmentFlag.AlignBottom)
        filters.addWidget(self.refresh_button, alignment=Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(filters)

        log_card = QFrame()
        log_card.setObjectName("managed_log_card")
        log_card.setProperty("role", "card")
        card_layout = QVBoxLayout(log_card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_actions = QHBoxLayout()
        card_actions.addStretch()
        self.export_button = QPushButton("진단 ZIP 내보내기")
        self.export_button.setObjectName("logs_export")
        self.export_button.clicked.connect(self._choose_export_destination)
        card_actions.addWidget(self.export_button)
        self.export_button.hide()
        card_layout.addLayout(card_actions)

        self.entries = QTableWidget(0, 6)
        self.entries.setObjectName("managed_log_entries")
        self.entries.setHorizontalHeaderLabels(("시간", "기능", "작업", "대상", "결과", "내용"))
        self.entries.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.entries.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.entries.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.entries.currentCellChanged.connect(self._selection_changed)
        self.entries.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        header = self.entries.horizontalHeader()
        for column, width in (
            (0, 174),
            (1, max(125, self.fontMetrics().horizontalAdvance("대시보드") + 58)),
            (2, 150),
            (4, max(100, self.fontMetrics().horizontalAdvance("성공") + 60)),
        ):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(column, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.entries.verticalHeader().setVisible(False)
        self.entries.verticalHeader().setDefaultSectionSize(40)
        self.entries.verticalHeader().setMinimumSectionSize(40)
        use_first_column_selection_bar(self.entries)
        card_layout.addWidget(self.entries, 1)

        details = QFrame()
        details.setObjectName("managed_log_detail")
        details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(12, 8, 12, 8)
        detail_layout.setSpacing(6)
        summary = QHBoxLayout()
        summary.setSpacing(12)
        self._detail_labels: dict[str, QLabel] = {}
        for name in ("레벨", "기능", "작업", "대상", "결과", "시간"):
            value = QLabel("-")
            value.setObjectName(f"managed_log_detail_{name}")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setAccessibleName(name)
            if name == "대상":
                value.setMinimumWidth(0)
                value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            if name == "결과":
                self.detail_result_icon = QLabel()
                summary.addWidget(self.detail_result_icon)
            summary.addWidget(value, 1 if name == "대상" else 0)
            self._detail_labels[name] = value
        self.copy_detail_button = QPushButton("상세 복사")
        self.copy_detail_button.setObjectName("managed_log_detail_copy")
        self.copy_detail_button.setProperty("size", "small")
        set_button_icon(self.copy_detail_button, "common-clipboard.svg")
        self.copy_detail_button.setEnabled(False)
        self.copy_detail_button.clicked.connect(self.copy_selected_detail)
        summary.addWidget(self.copy_detail_button)
        self.detail_toggle = QToolButton()
        self.detail_toggle.setObjectName("managed_log_detail_toggle")
        self.detail_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.detail_toggle.setCheckable(True)
        self.detail_toggle.setChecked(True)
        self.detail_toggle.toggled.connect(self._toggle_detail)
        summary.addWidget(self.detail_toggle)
        detail_layout.addLayout(summary)

        self.detail_body = QScrollArea()
        self.detail_body.setObjectName("managed_log_detail_body")
        self.detail_body.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_body.setWidgetResizable(True)
        self.detail_body.setMaximumHeight(166)
        self.detail_body.setMinimumHeight(150)
        body = QWidget()
        body.setObjectName("managed_log_detail_content")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        self.detail_message = QLabel("로그를 선택하면 안전하게 마스킹된 상세 정보가 표시됩니다.")
        self.detail_message.setObjectName("managed_log_detail_message")
        self.detail_message.setWordWrap(True)
        self.detail_message.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_message.setAccessibleName("메시지")
        body_layout.addWidget(self.detail_message)
        self.detail_metadata = QFrame()
        metadata_layout = QGridLayout(self.detail_metadata)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setHorizontalSpacing(12)
        metadata_layout.setVerticalSpacing(4)
        metadata_layout.setColumnStretch(1, 1)
        metadata_layout.setColumnStretch(3, 1)
        self._metadata_labels: dict[str, QLabel] = {}
        for index, caption in enumerate(_detail_metadata_values(None)):
            value = QLabel("-")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row, column = divmod(index, 2)
            metadata_layout.addWidget(QLabel(caption), row, column * 2, Qt.AlignmentFlag.AlignTop)
            metadata_layout.addWidget(value, row, column * 2 + 1)
            self._metadata_labels[caption] = value
        body_layout.addWidget(self.detail_metadata)
        self.detail_errors = QLabel()
        self.detail_errors.setObjectName("managed_log_error_info")
        self.detail_errors.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_errors.setWordWrap(True)
        self.detail_errors.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_errors.hide()
        body_layout.addWidget(self.detail_errors)
        body_layout.addStretch()
        self.detail_body.setWidget(body)
        detail_layout.addWidget(self.detail_body)
        self._toggle_detail(True)
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

    def _toggle_detail(self, expanded: bool) -> None:
        self.detail_body.setVisible(expanded)
        panel = self.detail_body.parentWidget()
        if panel is not None:
            panel_layout = panel.layout()
            if panel_layout is not None:
                panel_layout.activate()
            panel.updateGeometry()
        self.detail_toggle.setText("접기" if expanded else "펼치기")
        self.detail_toggle.setArrowType(
            Qt.ArrowType.UpArrow if expanded else Qt.ArrowType.DownArrow
        )
        self.detail_toggle.setAccessibleName("로그 상세 접기" if expanded else "로그 상세 펼치기")

    def _filter_combo(self, name: str, values: tuple[str, ...]) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(f"logs_filter_{name}")
        combo.addItems(values)
        return combo

    def _toggle_settings(self, expanded: bool) -> None:
        self.settings_toggle.setText("▾ 로그 설정" if expanded else "▸ 로그 설정")
        self.settings_panel.setVisible(expanded)

    def refresh(self) -> None:
        query = self._query()
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._runner.submit(
            lambda: (self._settings.get(), self._activity_logs.recent(query=query)),
            lambda value: self._loaded(value) if generation == self._refresh_generation else None,
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
            view_model = build_activity_log_row_view_model(entry)
            self.entries.setItem(row, 0, QTableWidgetItem(view_model.occurred_at))
            self.entries.setCellWidget(
                row, 1, icon_text(view_model.feature.icon, view_model.feature.text)
            )
            self.entries.setItem(row, 2, QTableWidgetItem(view_model.operation))
            self.entries.setItem(row, 3, QTableWidgetItem(view_model.target))
            self.entries.setCellWidget(row, 4, status_badge(view_model.result))
            self.entries.setItem(row, 5, QTableWidgetItem(view_model.message))
            self.entries.setRowHeight(row, 40)
            # Measure the styled cell widgets: fallback Korean fonts and padding
            # can be wider than page metrics. Reserve the table cell insets too.
            for column in (1, 4):
                widget = self.entries.cellWidget(row, column)
                widget.ensurePolished()
                for label in widget.findChildren(QLabel):
                    label.ensurePolished()
                    label.setMinimumWidth(label.sizeHint().width())
                cell_layout = widget.layout()
                if cell_layout is not None:
                    cell_layout.invalidate()
                self.entries.setColumnWidth(
                    column, max(self.entries.columnWidth(column), widget.sizeHint().width() + 24)
                )
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
            for value in self._detail_labels.values():
                value.setText("-")
            self.detail_message.setText("로그를 선택하면 안전하게 마스킹된 상세 정보가 표시됩니다.")
            for value in self._metadata_labels.values():
                value.setText("-")
            self.detail_result_icon.clear()
            self.detail_errors.clear()
            self.detail_errors.hide()
            self.copy_detail_button.setEnabled(False)
            return
        view_model = build_activity_log_row_view_model(entry)
        self._detail_labels["시간"].setText(view_model.occurred_at.rsplit(" ", 1)[-1])
        self._detail_labels["시간"].setToolTip(view_model.occurred_at)
        self._detail_labels["레벨"].setText(f"●  {view_model.level.text}")
        self._detail_labels["레벨"].setProperty("level", view_model.level.key)
        self._detail_labels["레벨"].style().unpolish(self._detail_labels["레벨"])
        self._detail_labels["레벨"].style().polish(self._detail_labels["레벨"])
        self._detail_labels["기능"].setText(view_model.feature.text)
        self._detail_labels["작업"].setText(view_model.operation)
        self._detail_labels["대상"].setText(view_model.target)
        self._detail_labels["대상"].setToolTip(view_model.target)
        self.detail_result_icon.setPixmap(
            gui_icon(view_model.result.icon or "", color=view_model.result.color).pixmap(18, 18)
        )
        self._detail_labels["결과"].setText(view_model.result.text)
        self._detail_labels["결과"].setStyleSheet(f"color: {view_model.result.color};")
        self._detail_labels["결과"].setProperty("status", view_model.result.key)
        self._detail_labels["결과"].style().unpolish(self._detail_labels["결과"])
        self._detail_labels["결과"].style().polish(self._detail_labels["결과"])
        self.detail_message.setText(view_model.message)
        for caption, text in _detail_metadata_values(view_model).items():
            self._metadata_labels[caption].setText(text)
        errors = view_model.error_details()
        self.detail_errors.setText(
            "오류 정보  ·  "
            + "  ·  ".join(f"{caption}: {text}" for caption, text in errors.items())
        )
        self.detail_errors.setVisible(bool(errors))
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

    def _query(self) -> ActivityLogQuery:
        period = self.period_filter.currentText()
        period_days = 1 if period == "오늘" else 7 if period == "최근 7일" else None
        level = self.level_filter.currentText()
        feature = self.feature_filter.currentText()
        feature_key = {
            "EC2": "ec2",
            "RDS": "rds",
            "S3": "s3",
            "Secrets": "secrets",  # pragma: allowlist secret
            "인증": "auth",
            "대시보드": "dashboard",
            "프로그램": "program",
        }.get(feature)
        return ActivityLogQuery(
            period_days=period_days,
            levels=() if level == "전체" else (level,),
            features=() if feature_key is None else (feature_key,),
            search=self.search.text(),
            errors_only=self.errors_only.isChecked(),
        )

    def _filter_changed(self, *_args: object) -> None:
        self.refresh()


def _detail_text(entry: ManagedLogEntry) -> str:
    view_model = build_activity_log_row_view_model(entry)
    retryable = "예" if entry.retryable is True else "아니요" if entry.retryable is False else "-"
    values = (
        ("시간", view_model.occurred_at),
        ("레벨", view_model.level.text),
        ("기능", view_model.feature.text),
        ("작업", view_model.operation),
        ("대상", view_model.target),
        ("결과", view_model.result.text),
        ("메시지", view_model.message),
        *_detail_metadata_values(view_model).items(),
        *view_model.error_details(include_success=True).items(),
        ("재시도 가능", retryable),
        ("상세 원인", entry.masked_detail or "-"),
        ("부가 정보", entry.metadata_json),
    )
    return "\n".join(f"{label}: {value}" for label, value in values)


def _detail_metadata_values(view_model: ActivityLogRowViewModel | None) -> dict[str, str]:
    fields = {
        "Operation ID": "operation_id",
        "Correlation ID": "correlation_id",
        "AWS 서비스": "aws_service",
        "AWS Action": "aws_action",
        "AWS Request ID": "aws_request_id",
        "단계": "phase",
    }
    return {
        caption: getattr(view_model, field) if view_model is not None else "-"
        for caption, field in fields.items()
    }
