"""S3 GUI adapter backed by shared Application Services."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.operations import OperationContext, ProgressEvent
from aws_connect.application.ports import S3Object
from aws_connect.application.s3_service import (
    DownloadPlan,
    DownloadSummary,
    S3LocationService,
    S3Service,
    SaveS3LocationRequest,
    UploadConflictPolicy,
    UploadPlan,
    UploadSummary,
)
from aws_connect.domain.errors import ApplicationError, AwsPermissionError, ConfigurationError
from aws_connect.domain.s3_location import S3Location
from aws_connect.presentation.gui.authenticated import AuthenticatedGuiRunner, MfaCodeProvider
from aws_connect.presentation.gui.icons import gui_icon, set_button_icon
from aws_connect.presentation.gui.page_layout import PAGE_SPACING, apply_page_layout, page_heading
from aws_connect.presentation.gui.s3_search import S3SearchDialog
from aws_connect.presentation.gui.table_selection import add_check_all_header
from aws_connect.presentation.gui.tasks import GuiTaskRunner, TaskHandle
from aws_connect.presentation.gui.upload_sources import (
    UploadQueueEntry,
    UploadSourceDialog,
    UploadSourcesList,
    format_size,
)
from aws_connect.presentation.gui.view_models import build_s3_progress_view_model

ConfirmUpload = Callable[[QWidget, UploadPlan], UploadConflictPolicy | None]
ConfirmObjectDelete = Callable[[QWidget, S3Object], bool]
ConfirmDownload = Callable[[QWidget, DownloadPlan], UploadConflictPolicy | None]
DownloadDestination = Callable[[QWidget], Path | None]


class _DropZone(QLabel):
    """Clickable label that keeps the page-level drag/drop contract."""

    clicked = Signal()

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class S3Page(QWidget):
    error_raised = Signal(object)
    notice_raised = Signal(str)

    def __init__(
        self,
        locations: S3LocationService,
        s3: S3Service,
        runner: GuiTaskRunner,
        confirm_upload: ConfirmUpload | None = None,
        authenticated: AuthenticatedOperationCoordinator | None = None,
        mfa_code_provider: MfaCodeProvider | None = None,
        confirm_delete: ConfirmObjectDelete | None = None,
        download_destination: DownloadDestination | None = None,
        confirm_download: ConfirmDownload | None = None,
    ) -> None:
        super().__init__()
        self._locations = locations
        self._s3 = s3
        self._runner = runner
        self._confirm_upload = confirm_upload or _confirm_upload
        self._confirm_delete = confirm_delete or _confirm_object_delete
        self._confirm_download = confirm_download or _confirm_download
        self._download_destination = download_destination or _choose_download_destination
        self._authenticated = (
            AuthenticatedGuiRunner(authenticated, runner, self, mfa_code_provider)
            if authenticated is not None and mfa_code_provider is not None
            else None
        )
        self._profile_id: int | None = None
        self._saved: list[S3Location] = []
        self._listed_objects: list[S3Object] = []
        self._selected_files: list[Path] = []
        self._upload_task: TaskHandle | None = None
        self._upload_in_progress = False
        self._download_task: TaskHandle | None = None
        self._download_in_progress = False
        self._download_states: dict[tuple[str, str], str] = {}
        self._download_selected: set[tuple[str, str]] = set()
        self._download_current: tuple[str, str] | None = None
        self._download_angle = 0
        self._download_spinner = QTimer(self)
        self._download_spinner.setInterval(100)
        self._download_spinner.timeout.connect(self._animate_downloads)
        self._shutdown_callbacks: list[Callable[[], None]] = []
        self._queue: dict[Path, UploadQueueEntry] = {}
        self._active_sources: set[Path] = set()
        self._hide_completed = False
        self._failed_only = False
        self._query: str | None = None
        self._recent_paths: list[tuple[str, str]] = []
        self._listing_generation = 0
        self._profile_generation = 0
        self._upload_generation = 0
        self._download_generation = 0
        self._preparing_upload = False
        self._upload_attempt = 1
        self._closing = False
        self.setAcceptDrops(True)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        apply_page_layout(layout)
        page_head = QHBoxLayout()
        heading_copy = page_heading(
            "S3 파일", "Bucket을 탐색하거나 현재 Prefix에 로컬 파일을 업로드합니다."
        )
        page_head.addLayout(heading_copy)
        page_head.addStretch()
        layout.addLayout(page_head)

        self.bucket = QLineEdit(self)
        self.bucket.setObjectName("s3_bucket_input")
        self.bucket.hide()
        self.prefix = QLineEdit(self)
        self.prefix.setObjectName("s3_prefix_input")
        self.prefix.hide()
        self.bucket_catalog = QComboBox()
        self.bucket_catalog.setObjectName("s3_bucket_catalog")
        self.bucket_catalog.setEditable(True)
        self.bucket_catalog.setMinimumWidth(380)
        self.bucket_catalog.setMaximumWidth(480)
        self.bucket_catalog.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.bucket_catalog.currentTextChanged.connect(self.bucket_catalog.setToolTip)
        self.bucket_catalog.activated.connect(
            lambda _index: self._catalog_bucket_selected(self.bucket_catalog.currentText())
        )
        editor = self.bucket_catalog.lineEdit()
        if editor is not None:
            editor.editingFinished.connect(
                lambda: self._catalog_bucket_selected(self.bucket_catalog.currentText())
            )

        self.upload = QPushButton("업로드")
        self.upload.setObjectName("s3_upload")
        self.upload.setProperty("variant", "primary")
        self.upload.clicked.connect(self.toggle_upload)
        page_head.addWidget(self.upload)

        content = QWidget()
        content.setObjectName("s3_page_content")
        cards = QVBoxLayout(content)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(PAGE_SPACING)
        browser_card = QFrame()
        browser_card.setObjectName("s3_browser_card")
        browser_card.setProperty("role", "card")
        right = QVBoxLayout(browser_card)
        right.setContentsMargins(18, 14, 18, 14)
        header = QHBoxLayout()
        heading = QLabel("S3 Bucket")
        heading.setObjectName("s3_section_title")
        header.addWidget(heading)
        header.addWidget(self.bucket_catalog)
        self.breadcrumb = QWidget()
        self.breadcrumb.setObjectName("s3_breadcrumb")
        self.breadcrumb_layout = QHBoxLayout(self.breadcrumb)
        self.breadcrumb_layout.setContentsMargins(4, 0, 4, 0)
        breadcrumb_scroll = QScrollArea()
        breadcrumb_scroll.setFrameShape(QFrame.Shape.NoFrame)
        breadcrumb_scroll.setWidgetResizable(True)
        breadcrumb_scroll.setWidget(self.breadcrumb)
        breadcrumb_scroll.setFixedHeight(48)
        header.addWidget(breadcrumb_scroll, 1)
        search = self._action_button("common-search.svg", "검색 / 경로 이동", self.open_search)
        search.setObjectName("s3_browse")
        header.addWidget(search)
        self.open_object_button = self._action_button(
            "s3-download.svg", "다운로드", self.download_selected_objects
        )
        header.addWidget(self.open_object_button)
        self.delete_object_button = self._action_button(
            "common-delete.svg", "선택 파일 삭제", self.delete_selected_object, danger=True
        )
        header.addWidget(self.delete_object_button)
        header.addWidget(self._action_button("common-refresh.svg", "새로고침", self.list_objects))
        right.addLayout(header)
        self.objects = QTableWidget(0, 7)
        self.objects.setObjectName("s3_objects")
        self.objects.setWordWrap(False)
        self.objects.setIconSize(QSize(22, 22))
        self.objects.setHorizontalHeaderLabels(
            ["", "이름", "유형", "크기", "수정 시간", "상태", "작업"]
        )
        self.objects.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.objects.cellDoubleClicked.connect(self._object_activated)
        self.objects.itemSelectionChanged.connect(self._sync_object_actions)
        self.objects.itemChanged.connect(lambda _item: self._sync_object_actions())
        self.objects.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.objects.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.objects.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate((32, 280, 80, 95, 170, 52, 62)):
            self.objects.setColumnWidth(col, width)
        self.objects.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.objects.verticalHeader().hide()
        self.objects.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.objects.verticalHeader().setDefaultSectionSize(38)
        self.objects.setMinimumHeight(250)
        add_check_all_header(self.objects)
        self.search_summary = QWidget()
        summary_layout = QHBoxLayout(self.search_summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        self.search_text = QLabel()
        self.search_text.setTextFormat(Qt.TextFormat.PlainText)
        summary_layout.addWidget(self.search_text, 1)
        clear_search = QPushButton("검색 해제")
        clear_search.clicked.connect(lambda: self._navigate_to_prefix(self.prefix.text()))
        summary_layout.addWidget(clear_search)
        right.addWidget(self.search_summary)
        self.search_summary.hide()
        right.addWidget(self.objects, 1)
        cards.addWidget(browser_card, 3)

        queue_card = QFrame()
        queue_card.setObjectName("s3_queue_card")
        queue_card.setProperty("role", "card")
        queue_layout = QVBoxLayout(queue_card)
        queue_layout.setContentsMargins(18, 12, 18, 12)
        self.sources_header = QWidget()
        sources_heading = QHBoxLayout(self.sources_header)
        sources_heading.setContentsMargins(0, 0, 0, 0)
        self.file_summary = QLabel("업로드 파일 (0개)")
        self.file_summary.setObjectName("s3_section_title")
        sources_heading.addWidget(self.file_summary, 1)
        self.add_sources_button = self._action_button(
            "folder-plus-solid-full.svg", "파일 / 폴더 추가", self.choose_sources
        )
        sources_heading.addWidget(self.add_sources_button)
        more = self._action_button("common-more.svg", "업로드 목록 메뉴", self.open_queue_menu)
        more.setObjectName("s3_queue_more")
        sources_heading.addWidget(more)
        queue_layout.addWidget(self.sources_header)
        self.upload_sources = UploadSourcesList(
            self.dragEnterEvent, self.dropEvent, self.choose_sources, self._runner
        )
        queue_layout.addWidget(self.upload_sources, 1)
        footer = QHBoxLayout()
        self.queue_summary = QLabel("0개 성공   0개 실패   0개 대기")
        footer.addWidget(self.queue_summary)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        footer.addWidget(self.progress, 1)
        queue_layout.addLayout(footer)
        self.upload_status = QLabel("")
        self.upload_status.setObjectName("s3_upload_status")
        self.upload_status.hide()
        queue_layout.addWidget(self.upload_status)
        cards.addWidget(queue_card, 2)
        self.drop_zone = _DropZone("")
        self.drop_zone.hide()
        self.drop_zone.clicked.connect(self.choose_sources)
        browser_scroll = QScrollArea()
        browser_scroll.setObjectName("s3_browser_scroll")
        browser_scroll.setFrameShape(QFrame.Shape.NoFrame)
        browser_scroll.setWidgetResizable(True)
        browser_scroll.setWidget(content)
        layout.addWidget(browser_scroll, 1)

        saved_panel = QFrame()
        saved_panel.setObjectName("s3_saved_locations_panel")
        saved_panel.setProperty("role", "secondary")
        saved_layout = QHBoxLayout(saved_panel)
        self.location_list = QListWidget()
        self.location_list.setObjectName("s3_saved_locations")
        self.location_list.setMaximumWidth(220)
        self.location_list.setMaximumHeight(112)
        self.location_list.currentRowChanged.connect(self._location_selected)
        saved_layout.addWidget(QLabel("저장 위치"))
        saved_layout.addWidget(self.location_list)
        form = QFormLayout()
        self.location_name = QLineEdit()
        form.addRow("이름", self.location_name)
        saved_layout.addLayout(form, 1)
        location_actions = QHBoxLayout()
        new_location = QPushButton("새 위치")
        save = QPushButton("저장")
        delete = QPushButton("삭제")
        set_button_icon(save, "common-save.svg")
        set_button_icon(delete, "common-delete.svg")
        new_location.clicked.connect(self.new_location)
        save.clicked.connect(self.save_location)
        delete.clicked.connect(self.delete_location)
        location_actions.addWidget(new_location)
        location_actions.addWidget(save)
        location_actions.addWidget(delete)
        up = QPushButton("상위 Prefix")
        up.clicked.connect(self.navigate_up)
        location_actions.addWidget(up)
        saved_layout.addLayout(location_actions)
        layout.addWidget(saved_panel)
        saved_panel.hide()
        self._render_breadcrumb()
        self._sync_upload_action()
        self._sync_object_actions()

    def _action_button(
        self, icon: str, tooltip: str, action: Callable[[], None], *, danger: bool = False
    ) -> QPushButton:
        button = QPushButton()
        button.setProperty("action_button", True)
        if danger:
            button.setProperty("variant", "danger")
        color = (
            "#ffffff"
            if danger
            else "#0055ff"
            if icon in {"s3-download.svg", "folder-plus-solid-full.svg"}
            else None
        )
        set_button_icon(button, icon, tooltip=tooltip, color=color)
        button.clicked.connect(action)
        return button

    def _menu_action(
        self, menu: QMenu, text: str, icon: str, *, danger: bool = False
    ) -> QWidgetAction:
        action = QWidgetAction(menu)
        action.setText(text)
        button = QPushButton(text)
        button.setObjectName("s3_menu_item")
        button.setProperty("danger", danger)
        set_button_icon(button, icon, color="#ff2638" if danger else None, size=18)
        action.setDefaultWidget(button)
        button.clicked.connect(action.trigger)
        button.clicked.connect(menu.close)

        def sync() -> None:
            button.setEnabled(action.isEnabled())
            button.setCheckable(action.isCheckable())
            button.setChecked(action.isChecked())

        action.changed.connect(sync)
        menu.addAction(action)
        return action

    def _danger_menu_action(self, menu: QMenu, text: str, callback: Callable[[], None]) -> None:
        self._menu_action(menu, text, "common-delete.svg", danger=True).triggered.connect(callback)

    def open_search(self) -> None:
        generation = self._profile_generation
        dialog = S3SearchDialog(
            self,
            self._current_bucket(),
            self.prefix.text(),
            [self.bucket_catalog.itemText(i) for i in range(self.bucket_catalog.count())],
            self._recent_paths,
        )
        result = dialog.exec()
        dialog.deleteLater()
        if result != QDialog.DialogCode.Accepted or generation != self._profile_generation:
            return
        bucket = dialog.bucket.currentText().strip()
        self.bucket.setText(bucket)
        with QSignalBlocker(self.bucket_catalog):
            self.bucket_catalog.setCurrentText(bucket)
        prefix = dialog.prefix.text().strip().strip("/")
        self.prefix.setText(f"{prefix}/" if prefix else "")
        self._query = dialog.query.text().strip() if dialog.tabs.currentIndex() else None
        self.list_objects()

    def _select_object_row(self, row: int) -> None:
        with QSignalBlocker(self.objects):
            for index in range(self.objects.rowCount()):
                item = self.objects.item(index, 0)
                if item is not None:
                    item.setCheckState(Qt.CheckState.Unchecked)
            self.objects.selectRow(row)
        self._sync_object_actions()

    def open_object_menu(self, row: int) -> None:
        self._select_object_row(row)
        selected = self._listed_objects[row]
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.setObjectName("s3_action_menu")
        download = self._menu_action(menu, "다운로드", "s3-download.svg")
        download.setEnabled(not self._download_in_progress)
        download.triggered.connect(self.download_selected_objects)
        rename = self._menu_action(menu, "이름 변경", "common-edit.svg")
        rename.setEnabled(not selected.is_prefix)
        rename.triggered.connect(self.rename_selected_object)
        copy = self._menu_action(menu, "복사 경로", "common-copy.svg")
        uri = f"s3://{self._current_bucket()}/{selected.key}"
        copy.triggered.connect(lambda: QApplication.clipboard().setText(uri))
        self._danger_menu_action(menu, "삭제", self.delete_selected_object)
        menu.actions()[-1].setEnabled(not selected.is_prefix)
        button = self.objects.cellWidget(row, 6)
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def rename_selected_object(self) -> None:
        selected = self._selected_objects()
        if self._profile_id is None or len(selected) != 1 or selected[0].is_prefix:
            return
        item = selected[0]
        profile_id, bucket, generation = (
            self._profile_id,
            self._current_bucket(),
            self._profile_generation,
        )
        name, accepted = QInputDialog.getText(
            self, "S3 파일 이름 변경", "새 파일 이름", text=item.key.rsplit("/", 1)[-1]
        )
        if not accepted or generation != self._profile_generation:
            return

        def action() -> str:
            return self._s3.rename_object(bucket, item.key, name, profile_id)

        def completed(_value: Any) -> None:
            self.notice_raised.emit("파일 이름을 변경했습니다.")
            if self._profile_id == profile_id:
                self.list_objects()

        if self._authenticated is None:
            self._runner.submit(action, completed, self.error_raised.emit)
        else:
            self._authenticated.submit(
                profile_id, action, completed, self.error_raised.emit, lambda: None
            )

    def open_queue_menu(self) -> None:
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.setObjectName("s3_action_menu")
        for label, icon, checked, callback in (
            ("완료 항목 숨기기", "common-eye.svg", self._hide_completed, self._set_hide_completed),
            (
                "실패 항목만 보기",
                "common-circle-xmark.svg",
                self._failed_only,
                self._set_failed_only,
            ),
        ):
            action = self._menu_action(menu, label, icon)
            action.setCheckable(True)
            action.setChecked(checked)
            action.toggled.connect(callback)
        self._danger_menu_action(menu, "목록 전체 비우기", self.clear_sources)
        menu.actions()[-1].setEnabled(not self._upload_in_progress and not self._preparing_upload)
        button = self.findChild(QPushButton, "s3_queue_more")
        if button is not None:
            menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _set_hide_completed(self, value: bool) -> None:
        self._hide_completed = value
        self._render_queue_state()

    def _set_failed_only(self, value: bool) -> None:
        self._failed_only = value
        self._render_queue_state()

    def set_profile(self, profile_id: int | None) -> None:
        self.cancel_upload()
        self.cancel_download()
        self._profile_generation += 1
        self._listing_generation += 1
        self._download_states.clear()
        self._download_selected.clear()
        self._download_spinner.stop()
        self._query = None
        self._recent_paths.clear()
        self._hide_completed = False
        self._failed_only = False
        self._queue.clear()
        self._selected_files.clear()
        self.bucket.clear()
        self.prefix.clear()
        self._listed_objects.clear()
        self._set_locations([])
        self._profile_id = profile_id
        self._render_sources()
        self.objects.setRowCount(0)
        self._use_direct_bucket_input()
        self.bucket_catalog.clear()
        self._render_breadcrumb()
        if profile_id is None:
            self._set_locations([])
        else:
            generation = self._profile_generation

            def loaded(value: Any) -> None:
                if generation == self._profile_generation:
                    self._set_locations(value)

            self._runner.submit(
                lambda: self._locations.list(profile_id),
                loaded,
                self.error_raised.emit,
            )
            self.load_buckets()

    def _set_locations(self, value: Any) -> None:
        self._saved = list(value)
        self.location_list.clear()
        self.location_list.addItems([item.name for item in self._saved])

    def _location_selected(self, row: int) -> None:
        if 0 <= row < len(self._saved):
            value = self._saved[row]
            self.location_name.setText(value.name)
            self.bucket.setText(value.bucket)
            self.prefix.setText(value.prefix)
            self._select_catalog_bucket(value.bucket)
            self._render_breadcrumb()

    def new_location(self) -> None:
        self.location_list.clearSelection()
        self.location_list.setCurrentRow(-1)
        self.location_name.clear()
        self.bucket.clear()
        self.prefix.clear()

    def save_location(self) -> None:
        if self._profile_id is None:
            self.error_raised.emit(ConfigurationError("profile.not_found", "No active profile"))
            return
        row = self.location_list.currentRow()
        location_id = self._saved[row].id if 0 <= row < len(self._saved) else None
        request = SaveS3LocationRequest(
            self._profile_id,
            self.location_name.text(),
            self._current_bucket(),
            self.prefix.text(),
            location_id,
        )
        operation = (
            (lambda: self._locations.update(request))
            if location_id
            else (lambda: self._locations.create(request))
        )
        self._runner.submit(
            operation, lambda _value: self._location_saved(), self.error_raised.emit
        )

    def _location_saved(self) -> None:
        self.notice_raised.emit("S3 저장 위치를 저장했습니다.")
        self.set_profile(self._profile_id)

    def delete_location(self) -> None:
        row = self.location_list.currentRow()
        if not (0 <= row < len(self._saved)) or self._profile_id is None:
            return
        selected = self._saved[row]
        self._runner.submit(
            lambda: self._locations.delete(selected.require_id(), self._profile_id),
            lambda _value: self._location_saved(),
            self.error_raised.emit,
        )

    def load_buckets(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        generation = self._profile_generation

        def action() -> list[str]:
            return self._s3.list_buckets(profile_id)

        def loaded(value: Any) -> None:
            if generation == self._profile_generation:
                self._buckets_loaded(value)

        def failed(error: ApplicationError) -> None:
            if generation == self._profile_generation:
                self._bucket_catalog_failed(error)

        if self._authenticated is None:
            self._runner.submit(action, loaded, failed)
        else:
            self._authenticated.submit(
                profile_id,
                action,
                loaded,
                failed,
                self._bucket_mfa_cancelled,
            )

    def _buckets_loaded(self, value: Any) -> None:
        buckets = [str(item) for item in value]
        if not buckets:
            self._use_direct_bucket_input()
            return
        current = self.bucket.text().strip()
        with QSignalBlocker(self.bucket_catalog):
            self.bucket_catalog.clear()
            self.bucket_catalog.addItems(buckets)
            self._select_catalog_bucket(current)
        self.bucket.setVisible(False)
        self.bucket_catalog.setVisible(True)
        self._catalog_bucket_selected(self.bucket_catalog.currentText())

    def _bucket_catalog_failed(self, error: ApplicationError) -> None:
        self._use_direct_bucket_input()
        if isinstance(error, AwsPermissionError):
            self.notice_raised.emit("Bucket 목록 권한이 없어 직접 입력 방식으로 전환했습니다.")
            return
        self.error_raised.emit(error)

    def _bucket_mfa_cancelled(self) -> None:
        self._use_direct_bucket_input()

    def _use_direct_bucket_input(self) -> None:
        self.bucket_catalog.setVisible(True)
        self.bucket_catalog.setEditable(True)
        self.bucket.hide()

    def _select_catalog_bucket(self, bucket: str) -> None:
        index = self.bucket_catalog.findText(bucket)
        if index >= 0:
            self.bucket_catalog.setCurrentIndex(index)

    def _catalog_bucket_selected(self, bucket: str) -> None:
        if bucket:
            if bucket != self.bucket.text():
                self.prefix.clear()
                self._query = None
            self.bucket.setText(bucket)
            self._render_breadcrumb()
            if self._profile_id is not None and not self.bucket_catalog.isHidden():
                self.list_objects()

    def _current_bucket(self) -> str:
        return self.bucket.text().strip() or self.bucket_catalog.currentText().strip()

    def _render_breadcrumb(self) -> None:
        while self.breadcrumb_layout.count():
            item = self.breadcrumb_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        bucket = self._current_bucket().strip() or "Bucket"
        root = QToolButton()
        root.setText("/")
        root.setToolTip(bucket)
        root.clicked.connect(lambda _checked=False: self._navigate_to_prefix(""))
        self.breadcrumb_layout.addWidget(root)
        current = ""
        for segment in filter(None, self.prefix.text().strip("/").split("/")):
            self.breadcrumb_layout.addWidget(QLabel("›"))
            current = f"{current}{segment}/"
            button = QToolButton()
            button.setText(segment)
            button.clicked.connect(
                lambda _checked=False, target=current: self._navigate_to_prefix(target)
            )
            self.breadcrumb_layout.addWidget(button)
        self.breadcrumb_layout.addStretch()

    def _navigate_to_prefix(self, prefix: str) -> None:
        self._query = None
        self.prefix.setText(prefix)
        self._render_breadcrumb()
        self.list_objects()

    def list_objects(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        bucket = self._current_bucket()
        prefix = self.prefix.text()
        query = self._query
        self._listing_generation += 1
        generation = self._listing_generation
        self._listed_objects.clear()
        self.objects.setRowCount(0)
        self._sync_object_actions()
        self._render_breadcrumb()
        self._render_queue_state()
        location = (bucket, prefix)
        if bucket:
            self._recent_paths = [location] + [p for p in self._recent_paths if p != location][:4]

        def action() -> list[S3Object]:
            return self._s3.list_objects(
                bucket, prefix, profile_id, **({"query": query} if query is not None else {})
            )

        def loaded(value: Any) -> None:
            if generation == self._listing_generation:
                self._objects_loaded(value)

        def failed(error: ApplicationError) -> None:
            if generation == self._listing_generation:
                self.error_raised.emit(error)

        if self._authenticated is None:
            self._runner.submit(action, loaded, failed)
        else:
            self._authenticated.submit(
                profile_id,
                action,
                loaded,
                failed,
                lambda: None,
            )

    def _objects_loaded(self, value: Any) -> None:
        self._listed_objects = list(value)
        self.search_summary.setVisible(self._query is not None)
        self.search_text.setText(f"검색 결과: {self._query} · {len(self._listed_objects)}개")
        with QSignalBlocker(self.objects):
            self.objects.setRowCount(0)
            self.objects.setRowCount(len(self._listed_objects))
            for row, item in enumerate(self._listed_objects):
                check = QTableWidgetItem()
                check.setCheckState(Qt.CheckState.Unchecked)
                self.objects.setItem(row, 0, check)
                name = QTableWidgetItem(_object_name(item, self.prefix.text()))
                name.setIcon(
                    gui_icon(
                        "file-folder.svg"
                        if item.is_prefix
                        else "file-image.svg"
                        if _file_type(item.key).startswith("image/")
                        else "file-default.svg",
                        color="#ffad32" if item.is_prefix else "#153750",
                    )
                )
                name.setToolTip(f"s3://{self._current_bucket()}/{item.key}")
                self.objects.setItem(row, 1, name)
                self.objects.setItem(row, 2, QTableWidgetItem("폴더" if item.is_prefix else "파일"))
                self.objects.setItem(
                    row, 3, QTableWidgetItem("-" if item.is_prefix else format_size(item.size))
                )
                self.objects.setItem(
                    row,
                    4,
                    QTableWidgetItem(
                        item.last_modified.strftime("%Y-%m-%d %H:%M:%S")
                        if item.last_modified
                        else "-"
                    ),
                )
                button = self._action_button(
                    "common-more.svg", "객체 작업", partial(self.open_object_menu, row)
                )
                self.objects.setCellWidget(row, 6, button)
                status = QLabel()
                status.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.objects.setCellWidget(row, 5, status)
        self._render_download_states()
        self._sync_object_actions()

    def _animate_downloads(self) -> None:
        self._download_angle = (self._download_angle + 30) % 360
        self._render_download_states()

    def _render_download_states(self) -> None:
        for row, item in enumerate(self._listed_objects):
            label = self.objects.cellWidget(row, 5)
            if not isinstance(label, QLabel):
                continue
            state = self._download_states.get((self._current_bucket(), item.key), "")
            label.clear()
            label.setToolTip(state)
            label.setAccessibleName(state or "다운로드 상태 없음")
            if state == "다운로드 중":
                pixmap = gui_icon("common-refresh.svg", color="#0055ff").pixmap(18, 18)
                label.setPixmap(
                    pixmap.transformed(
                        QTransform().rotate(self._download_angle),
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            elif state in {"성공", "실패"}:
                label.setPixmap(
                    gui_icon(
                        "common-circle-check.svg" if state == "성공" else "common-circle-xmark.svg",
                        color="#009e55" if state == "성공" else "#ff2638",
                    ).pixmap(18, 18)
                )
        if not any(value == "다운로드 중" for value in self._download_states.values()):
            self._download_spinner.stop()

    def _finish_download_states(self, state: str) -> None:
        if self._download_generation != self._profile_generation:
            return
        for key in self._download_selected:
            if self._download_states.get(key) == "다운로드 중":
                affected = (
                    self._download_current is None
                    or key == self._download_current
                    or (
                        key[1].endswith("/")
                        and key[0] == self._download_current[0]
                        and self._download_current[1].startswith(key[1])
                    )
                )
                self._download_states[key] = state if state != "실패" or affected else ""
        if self._download_current is not None and state == "실패":
            self._download_states[self._download_current] = state
        self._download_spinner.stop()
        self._render_download_states()

    def _sync_object_actions(self) -> None:
        selected = self._selected_objects()
        self.open_object_button.setEnabled(bool(selected) and not self._download_in_progress)
        self.delete_object_button.setEnabled(len(selected) == 1 and not selected[0].is_prefix)

    def _selected_objects(self) -> list[S3Object]:
        checked = [
            row
            for row in range(self.objects.rowCount())
            if (item := self.objects.item(row, 0)) is not None
            and item.checkState() == Qt.CheckState.Checked
        ]
        rows = checked or sorted(
            index.row() for index in self.objects.selectionModel().selectedRows()
        )
        return [self._listed_objects[row] for row in rows if 0 <= row < len(self._listed_objects)]

    def download_selected_objects(self) -> None:
        selected = tuple(self._selected_objects())
        if self._profile_id is None or not selected or self._download_in_progress:
            return
        profile_id, bucket, generation = (
            self._profile_id,
            self._current_bucket(),
            self._profile_generation,
        )
        destination_root = self._download_destination(self)
        if destination_root is None or generation != self._profile_generation:
            return
        self._download_generation = generation
        self._download_selected = {(bucket, item.key) for item in selected}
        self._download_current = None
        for key in self._download_selected:
            self._download_states[key] = "다운로드 중"
        self._download_spinner.start()
        self._render_download_states()
        self._download_in_progress = True
        self.open_object_button.setEnabled(False)

        def action() -> DownloadPlan:
            return self._s3.prepare_download(selected, destination_root, bucket, profile_id)

        if self._authenticated is None:
            self._runner.submit(action, self._download_prepared, self._download_failed)
        else:
            self._authenticated.submit(
                profile_id,
                action,
                self._download_prepared,
                self._download_failed,
                self._download_mfa_cancelled,
            )

    def _download_prepared(self, value: Any) -> None:
        if self._download_generation != self._profile_generation or self._closing:
            self._download_cancelled()
            return
        plan: DownloadPlan = value
        policy = self._confirm_download(self, plan)
        if policy is None or self._download_generation != self._profile_generation or self._closing:
            self._finish_download_states("")
            self._download_in_progress = False
            self._sync_object_actions()
            self._finish_shutdown()
            return
        self._download_in_progress = True
        self._sync_object_actions()
        if self._authenticated is None:
            handle = self._runner.submit_cancellable(
                lambda token, progress: self._s3.download(
                    plan, OperationContext(cancellation=token, progress=progress), policy=policy
                ),
                self._download_completed,
                self._download_failed,
                self._progressed,
            )
        else:
            handle = self._authenticated.submit_long(
                plan.profile_id,
                lambda context: self._s3.download(plan, context, policy=policy),
                self._download_completed,
                self._download_failed,
                self._progressed,
                self._download_cancelled,
            )
        if self._download_in_progress:
            self._download_task = handle

    def _download_completed(self, value: Any) -> None:
        summary: DownloadSummary = value
        self._finish_download_states("성공" if not summary.skipped else "")
        self._download_in_progress = False
        self._download_task = None
        self._sync_object_actions()
        self.notice_raised.emit(
            f"S3 다운로드를 완료했습니다. ({len(summary.downloaded)}개, "
            f"무시 {len(summary.skipped)}개)"
        )
        self._finish_shutdown()

    def _download_failed(self, error: ApplicationError) -> None:
        self._finish_download_states("실패")
        self._download_in_progress = False
        self._download_task = None
        self._sync_object_actions()
        self.error_raised.emit(error)
        self._finish_shutdown()

    def _download_cancelled(self) -> None:
        self._finish_download_states("")
        self._download_in_progress = False
        self._download_task = None
        self._sync_object_actions()
        self.upload_status.setText("S3 다운로드를 취소했습니다.")
        self.notice_raised.emit("S3 다운로드를 취소했습니다.")
        self._finish_shutdown()

    def _download_mfa_cancelled(self) -> None:
        self._finish_download_states("")
        self._download_in_progress = False
        self._download_task = None
        self._sync_object_actions()
        self.upload_status.setText("MFA 인증을 취소했습니다.")
        self._finish_shutdown()

    def delete_selected_object(self) -> None:
        selected_objects = self._selected_objects()
        if self._profile_id is None or len(selected_objects) != 1:
            return
        selected = selected_objects[0]
        profile_id, bucket, generation = (
            self._profile_id,
            self._current_bucket(),
            self._profile_generation,
        )
        if selected.is_prefix or not self._confirm_delete(self, selected):
            return
        if generation != self._profile_generation:
            return
        self.delete_object_button.setEnabled(False)

        def action() -> None:
            self._s3.delete_object(bucket, selected.key, profile_id)

        if self._authenticated is None:
            self._runner.submit(action, self._object_deleted, self._object_delete_failed)
        else:
            self._authenticated.submit(
                profile_id,
                action,
                self._object_deleted,
                self._object_delete_failed,
                self._sync_object_actions,
            )

    def _object_deleted(self, _value: Any) -> None:
        self.notice_raised.emit("S3 객체를 삭제했습니다.")
        self.list_objects()

    def _object_delete_failed(self, error: ApplicationError) -> None:
        self._sync_object_actions()
        self.error_raised.emit(error)

    def _object_activated(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._listed_objects) and self._listed_objects[row].is_prefix:
            self._query = None
            self.prefix.setText(self._listed_objects[row].key)
            self._render_breadcrumb()
            self.list_objects()

    def navigate_up(self) -> None:
        current = self.prefix.text().strip().rstrip("/")
        parent = current.rpartition("/")[0]
        self._navigate_to_prefix(f"{parent}/" if parent else "")

    def choose_sources(self) -> None:
        if self._upload_in_progress or self._preparing_upload:
            return
        dialog = UploadSourceDialog(self)
        if dialog.exec() == QFileDialog.DialogCode.Accepted:
            self.add_sources(dialog.paths)

    def set_files(self, values: Sequence[Path]) -> None:
        """Compatibility helper for adapters that replace the current file selection."""

        if self._upload_in_progress or self._preparing_upload:
            return
        self._selected_files.clear()
        self._queue.clear()
        self.add_sources(values)

    def add_sources(self, values: Sequence[Path]) -> None:
        if self._upload_in_progress or self._preparing_upload:
            return
        known = {value.expanduser().resolve() for value in self._selected_files}
        for value in values:
            source = Path(value).expanduser().resolve()
            if source in known or not (source.is_file() or source.is_dir()):
                continue
            known.add(source)
            self._selected_files.append(source)
            self._queue[source] = UploadQueueEntry(source)
        self._render_sources()

    def remove_selected_sources(self) -> None:
        if self._upload_in_progress or self._preparing_upload:
            return
        rows = sorted({item.row() for item in self.upload_sources.selectedItems()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._selected_files):
                source = self._selected_files.pop(row)
                self._queue.pop(source, None)
        self._render_sources()

    def clear_sources(self) -> None:
        if self._upload_in_progress or self._preparing_upload:
            return
        self._selected_files.clear()
        self._queue.clear()
        self._render_sources()

    def remove_source(self, source: Path) -> None:
        if self._upload_in_progress or self._preparing_upload:
            return
        if source in self._selected_files:
            self._selected_files.remove(source)
            self._queue.pop(source, None)
            self._render_sources()

    def _render_sources(self) -> None:
        self.upload_sources.clear()
        for source in self._selected_files:
            mime_type = "폴더" if source.is_dir() else _file_type(source.name)
            self.upload_sources.add_entry(
                self._queue[source], mime_type, self.remove_source, self.retry_source
            )
        count = len(self._selected_files)
        self.file_summary.setText(f"업로드 파일 ({count}개)")
        self.drop_zone.hide()
        self.sources_header.show()
        self.upload_sources.show()
        self._sync_upload_action()
        self._render_queue_state()

    def _render_queue_state(self) -> None:
        entries = list(self._queue.values())
        for row, entry in enumerate(entries):
            if row >= self.upload_sources.rowCount():
                break
            self.upload_sources.update_entry(
                row, entry, self._upload_in_progress or self._preparing_upload
            )
            hidden = (self._hide_completed and entry.state in {"성공", "무시"}) or (
                self._failed_only and entry.state != "실패"
            )
            self.upload_sources.setRowHidden(row, hidden)
        success = sum(e.state == "성공" for e in entries)
        failed = sum(e.state == "실패" for e in entries)
        waiting = sum(e.state == "대기" for e in entries)
        skipped = sum(e.state == "무시" for e in entries)
        self.queue_summary.setText(
            f"{success}개 성공   {failed}개 실패   {waiting}개 대기"
            + (f"   {skipped}개 무시" if skipped else "")
        )
        percent = int(sum(e.percent for e in entries) / len(entries)) if entries else 0
        self.progress.setValue(percent)
        self.progress.setFormat(f"전체 진행률 %p% ({success + skipped}/{len(entries)})")

    def retry_source(self, source: Path) -> None:
        entry = self._queue.get(source)
        if entry is not None and entry.state == "실패" and not self._upload_in_progress:
            self.prepare_upload(source)

    def toggle_upload(self) -> None:
        if self._upload_in_progress:
            self.upload.setText("업로드 취소")
            self.cancel_upload()
        else:
            self.upload.setText("업로드")
            self.prepare_upload()

    def _sync_upload_action(self) -> None:
        self.upload.setText("업로드 취소" if self._upload_in_progress else "업로드")
        if self._upload_in_progress:
            set_button_icon(self.upload, "common-stop.svg", tooltip="업로드 취소", color="#ffffff")
            self.upload.setProperty("variant", "danger")
            self.upload.setEnabled(True)
        else:
            set_button_icon(self.upload, "s3-upload.svg", tooltip="업로드", color="#ffffff")
            self.upload.setProperty("variant", "primary")
            self.upload.setEnabled(
                self._profile_id is not None
                and not self._preparing_upload
                and any(e.state in {"대기", "실패", "취소"} for e in self._queue.values())
            )
        self.add_sources_button.setEnabled(
            not self._upload_in_progress and not self._preparing_upload
        )
        self.upload.style().unpolish(self.upload)
        self.upload.style().polish(self.upload)

    def prepare_upload(self, retry: Path | None = None) -> None:
        if self._profile_id is None or self._upload_in_progress or self._preparing_upload:
            return
        self._upload_attempt = self._upload_attempt + 1 if retry is not None else 1
        profile_id = self._profile_id
        sources = (
            (retry,)
            if retry
            else tuple(
                e.source for e in self._queue.values() if e.state in {"대기", "실패", "취소"}
            )
        )
        if not sources:
            return
        bucket = self._current_bucket()
        prefix = self.prefix.text()
        groups: dict[tuple[str, str], list[Path]] = {}
        for source in sources:
            entry = self._queue[source]
            target_bucket, target_prefix = bucket, prefix
            if entry.target:
                location = entry.target.removeprefix("s3://")
                target_bucket, _, key = location.partition("/")
                target_prefix = key.rpartition("/")[0]
            groups.setdefault((target_bucket.strip(), target_prefix.strip().strip("/")), []).append(
                source
            )
        self._upload_generation = self._profile_generation
        self._preparing_upload = True
        self._active_sources = set(sources)
        self._sync_upload_action()
        self._render_queue_state()

        def action() -> UploadPlan:
            plans = [
                self._s3.prepare_upload(tuple(paths), b, p, profile_id)
                for (b, p), paths in groups.items()
            ]
            if len(plans) == 1:
                return plans[0]
            return UploadPlan(
                plans[0].profile_id,
                plans[0].region,
                tuple(item for plan in plans for item in plan.items),
            )

        if self._authenticated is None:
            self._runner.submit(action, self._upload_prepared, self._upload_failed)
        else:
            self._authenticated.submit(
                profile_id,
                action,
                self._upload_prepared,
                self._upload_failed,
                self._mfa_cancelled,
            )

    def _upload_prepared(self, value: Any) -> None:
        self._preparing_upload = False
        if self._upload_generation != self._profile_generation or self._closing:
            self._sync_upload_action()
            self._finish_shutdown()
            return
        plan: UploadPlan = value
        policy = self._confirm_upload(self, plan)
        if policy is None or self._upload_generation != self._profile_generation or self._closing:
            self._sync_upload_action()
            self._finish_shutdown()
            return
        self._upload_in_progress = True
        # Expand folder selections from the service's canonical preflight, never in GUI code.
        retained = {
            source: entry
            for source, entry in self._queue.items()
            if source not in self._active_sources
        }
        for item in plan.items:
            retained[item.source] = UploadQueueEntry(item.source, item.uri, item.size)
        self._queue = retained
        self._selected_files = list(retained)
        self._active_sources = {item.source for item in plan.items}
        self._render_sources()
        self._sync_upload_action()
        if self._authenticated is None:
            handle = self._runner.submit_cancellable(
                lambda token, progress: self._s3.upload(
                    plan,
                    policy=policy,
                    context=OperationContext(
                        cancellation=token, progress=progress, attempt=self._upload_attempt
                    ),
                ),
                self._upload_completed,
                self._upload_failed,
                self._progressed,
            )
        else:
            handle = self._authenticated.submit_long(
                plan.profile_id,
                lambda context: self._s3.upload(
                    plan, policy=policy, context=replace(context, attempt=self._upload_attempt)
                ),
                self._upload_completed,
                self._upload_failed,
                self._progressed,
                self._upload_cancelled,
            )
        # Real runners return before callbacks, while unit adapters may complete
        # inline. Never resurrect a handle already completed by an inline callback.
        if self._upload_in_progress:
            self._upload_task = handle

    def _progressed(self, value: Any) -> None:
        event: ProgressEvent = value
        if event.message_code.startswith("s3.download"):
            if self._download_generation != self._profile_generation:
                return
            if event.target and event.target.startswith("s3://"):
                bucket, _, key = event.target[5:].partition("/")
                self._download_current = (bucket, key)
                self._download_selected.add((bucket, key))
                state = {"s3.download.item.completed": "성공", "s3.download.item.skipped": ""}.get(
                    event.message_code, "다운로드 중"
                )
                self._download_states[(bucket, key)] = state
                self._render_download_states()
            self.upload_status.setText(f"다운로드 중: {event.target or ''}")
            return
        if (
            event.message_code.startswith("s3.upload")
            and self._upload_generation != self._profile_generation
        ):
            return
        if event.message_code.startswith("s3.upload.item."):
            for entry in self._queue.values():
                if entry.target != event.target:
                    continue
                suffix = event.message_code.rsplit(".", 1)[-1]
                entry.state = {
                    "started": "진행 중",
                    "progress": "진행 중",
                    "completed": "성공",
                    "skipped": "무시",
                }[suffix]
                if suffix == "started":
                    entry.started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry.percent = (
                    100
                    if suffix in {"completed", "skipped"}
                    else min(99, int((event.completed or 0) * 100 / event.total))
                    if event.total
                    else 0
                )
            self._render_queue_state()
            return
        view = build_s3_progress_view_model(event)
        if view.percent is not None and not self._queue:
            self.progress.setValue(view.percent)
        if event.message_code.startswith("s3.download") and event.target:
            self.upload_status.setText(f"다운로드 중: {event.target}")
        else:
            self.upload_status.setText(view.status_text)

    def _upload_completed(self, value: Any) -> None:
        summary: UploadSummary = value
        self._upload_in_progress = False
        self._upload_task = None
        if self._upload_generation == self._profile_generation:
            for entry in self._queue.values():
                if entry.target in summary.uploaded:
                    entry.state, entry.percent = "성공", 100
                elif entry.target in summary.skipped:
                    entry.state, entry.percent = "무시", 100
            self._render_queue_state()
        self._sync_upload_action()
        skipped = f", 무시 {len(summary.skipped)}개" if summary.skipped else ""
        self.notice_raised.emit(f"S3 업로드를 완료했습니다. ({len(summary.uploaded)}개{skipped})")
        self.list_objects()
        self._finish_shutdown()

    def _upload_failed(self, error: ApplicationError) -> None:
        self._preparing_upload = False
        self._upload_in_progress = False
        self._upload_task = None
        if self._upload_generation == self._profile_generation:
            running = any(e.state == "진행 중" for e in self._queue.values())
            for entry in self._queue.values():
                if entry.source in self._active_sources and (
                    entry.state == "진행 중" or (not running and entry.state == "대기")
                ):
                    entry.state = "실패"
            self._render_queue_state()
        self._sync_upload_action()
        self.error_raised.emit(error)
        self._finish_shutdown()

    def _mfa_cancelled(self) -> None:
        self._preparing_upload = False
        self._upload_in_progress = False
        self._upload_task = None
        self._sync_upload_action()
        self.upload_status.setText("MFA 인증을 취소했습니다.")
        self._finish_shutdown()

    def _upload_cancelled(self) -> None:
        self._upload_in_progress = False
        self._upload_task = None
        if self._upload_generation == self._profile_generation:
            for entry in self._queue.values():
                if entry.state == "진행 중":
                    entry.state = "취소"
            self._render_queue_state()
        self._sync_upload_action()
        self.upload_status.setText("S3 업로드를 취소했습니다.")
        self.notice_raised.emit("S3 업로드를 취소했습니다.")
        self._finish_shutdown()

    def cancel_upload(self) -> None:
        if self._upload_task is not None:
            self._upload_task.cancel()

    def cancel_download(self) -> None:
        if self._download_task is not None:
            self._download_task.cancel()

    def shutdown(self, completed: Callable[[], None]) -> None:
        self._closing = True
        if (
            not self._upload_in_progress
            and not self._download_in_progress
            and not self._preparing_upload
        ):
            completed()
            return
        self._shutdown_callbacks.append(completed)
        self.cancel_upload()
        self.cancel_download()

    def _finish_shutdown(self) -> None:
        if self._upload_in_progress or self._download_in_progress or self._preparing_upload:
            return
        callbacks, self._shutdown_callbacks = self._shutdown_callbacks, []
        for callback in callbacks:
            callback()

    def dragEnterEvent(self, event: Any) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:  # noqa: N802
        self.add_sources(
            [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        )
        event.acceptProposedAction()


def _confirm_upload(parent: QWidget, plan: UploadPlan) -> UploadConflictPolicy | None:
    return _confirm_conflicts(parent, tuple(item.source.name for item in plan.items if item.exists))


def _confirm_download(parent: QWidget, plan: DownloadPlan) -> UploadConflictPolicy | None:
    return _confirm_conflicts(
        parent, tuple(item.destination.name for item in plan.items if item.exists)
    )


def _confirm_conflicts(parent: QWidget, existing: tuple[str, ...]) -> UploadConflictPolicy | None:
    if not existing:
        return UploadConflictPolicy.SKIP_EXISTING
    representative = existing[0]
    remainder = len(existing) - 1
    summary = (
        f"{representative} 외 {remainder}건의 중복 파일이 있습니다."
        if remainder
        else f"{representative} 파일이 이미 존재합니다."
    )
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("중복 파일")
    dialog.setText(summary)
    overwrite = dialog.addButton("덮어쓰기", QMessageBox.ButtonRole.DestructiveRole)
    skip = dialog.addButton("무시", QMessageBox.ButtonRole.AcceptRole)
    dialog.addButton("취소", QMessageBox.ButtonRole.RejectRole)
    dialog.exec()
    if dialog.clickedButton() is overwrite:
        return UploadConflictPolicy.OVERWRITE
    if dialog.clickedButton() is skip:
        return UploadConflictPolicy.SKIP_EXISTING
    return None


def _confirm_object_delete(parent: QWidget, item: S3Object) -> bool:
    answer = QMessageBox.warning(
        parent,
        "S3 객체 삭제",
        f"s3://.../{item.key} 객체를 삭제할까요? 이 작업은 되돌릴 수 없습니다.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def _choose_download_destination(parent: QWidget) -> Path | None:
    selected = QFileDialog.getExistingDirectory(parent, "S3 다운로드 폴더 선택")
    return Path(selected) if selected else None


def _object_name(item: S3Object, prefix: str) -> str:
    relative = item.key[len(prefix) :] if prefix and item.key.startswith(prefix) else item.key
    return relative.rstrip("/") or item.key.rstrip("/")


def _object_type(item: S3Object) -> str:
    if item.is_prefix:
        return "폴더"
    return _file_type(item.key)


def _file_type(name: str) -> str:
    """Use the same MIME classification for remote objects and local upload cards."""
    mime_type, _encoding = mimetypes.guess_type(name)
    return mime_type or "파일"
