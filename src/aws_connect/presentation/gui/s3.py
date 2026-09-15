"""S3 GUI adapter backed by shared Application Services."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
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
from aws_connect.presentation.gui.table_selection import use_first_column_selection_bar
from aws_connect.presentation.gui.tasks import GuiTaskRunner, TaskHandle
from aws_connect.presentation.gui.view_models import build_s3_progress_view_model

ConfirmUpload = Callable[[QWidget, UploadPlan], UploadConflictPolicy | None]
ConfirmObjectDelete = Callable[[QWidget, S3Object], bool]
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
    ) -> None:
        super().__init__()
        self._locations = locations
        self._s3 = s3
        self._runner = runner
        self._confirm_upload = confirm_upload or _confirm_upload
        self._confirm_delete = confirm_delete or _confirm_object_delete
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
        self._shutdown_callbacks: list[Callable[[], None]] = []
        self.setAcceptDrops(True)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 40)
        layout.setSpacing(16)
        page_head = QHBoxLayout()
        heading_copy = QVBoxLayout()
        title = QLabel("S3 파일")
        title.setObjectName("page_title")
        subtitle = QLabel("Bucket을 탐색하거나 현재 Prefix에 로컬 파일을 업로드합니다.")
        subtitle.setObjectName("page_subtitle")
        heading_copy.addWidget(title)
        heading_copy.addWidget(subtitle)
        page_head.addLayout(heading_copy)
        page_head.addStretch()
        layout.addLayout(page_head)
        layout.addSpacing(8)

        toolbar = QFrame()
        toolbar.setObjectName("s3_toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        bucket_group = QVBoxLayout()
        bucket_group.addWidget(QLabel("Bucket"))
        bucket_row = QHBoxLayout()
        self.bucket = QLineEdit()
        self.bucket.setObjectName("s3_bucket_input")
        self.bucket_catalog = QComboBox()
        self.bucket_catalog.setObjectName("s3_bucket_catalog")
        self.bucket_catalog.setVisible(False)
        self.bucket_catalog.currentTextChanged.connect(self._catalog_bucket_selected)
        bucket_row.addWidget(self.bucket, 1)
        bucket_row.addWidget(self.bucket_catalog, 1)
        bucket_group.addLayout(bucket_row)
        toolbar_layout.addLayout(bucket_group, 1)
        prefix_group = QVBoxLayout()
        prefix_group.addWidget(QLabel("Prefix"))
        self.prefix = QLineEdit()
        self.prefix.setObjectName("s3_prefix_input")
        prefix_group.addWidget(self.prefix)
        toolbar_layout.addLayout(prefix_group, 2)
        browse = QPushButton("이동")
        browse.setObjectName("s3_browse")
        browse.clicked.connect(self.list_objects)
        toolbar_layout.addWidget(browse, alignment=Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(toolbar)

        browser_card = QFrame()
        browser_card.setObjectName("s3_browser_card")
        browser_card.setProperty("role", "card")
        right = QVBoxLayout(browser_card)
        right.setContentsMargins(20, 20, 20, 20)
        right.setSpacing(12)
        self.breadcrumb = QWidget()
        self.breadcrumb.setObjectName("s3_breadcrumb")
        self.breadcrumb_layout = QHBoxLayout(self.breadcrumb)
        self.breadcrumb_layout.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.breadcrumb)
        self.objects = QTableWidget(0, 4)
        self.objects.setObjectName("s3_objects")
        self.objects.setHorizontalHeaderLabels(["이름", "유형", "크기", "수정 시간"])
        self.objects.cellDoubleClicked.connect(self._object_activated)
        self.objects.itemSelectionChanged.connect(self._sync_object_actions)
        self.objects.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.objects.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.objects.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.objects.verticalHeader().setDefaultSectionSize(46)
        use_first_column_selection_bar(self.objects)
        right.addWidget(self.objects, 1)
        self.drop_zone = _DropZone("파일을 여기에 놓거나 클릭하여 선택")
        self.drop_zone.setObjectName("s3_drop_zone")
        self.drop_zone.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_zone.setMinimumHeight(0)
        self.drop_zone.clicked.connect(self.choose_sources)
        right.addWidget(self.drop_zone)
        self.upload_sources = QListWidget()
        self.upload_sources.setObjectName("s3_upload_sources")
        self.upload_sources.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.upload_sources.setMaximumHeight(112)
        right.addWidget(self.upload_sources)
        file_row = QHBoxLayout()
        self.file_summary = QLabel("선택한 파일이 없습니다.")
        self.file_summary.setObjectName("s3_file_summary")
        file_row.addWidget(self.file_summary, 1)
        remove_sources = QPushButton("선택 제거")
        remove_sources.clicked.connect(self.remove_selected_sources)
        file_row.addWidget(remove_sources)
        clear_sources = QPushButton("전체 비우기")
        clear_sources.clicked.connect(self.clear_sources)
        file_row.addWidget(clear_sources)
        self.open_object_button = QPushButton("다운로드")
        self.open_object_button.setEnabled(False)
        self.open_object_button.clicked.connect(self.download_selected_objects)
        file_row.addWidget(self.open_object_button)
        self.delete_object_button = QPushButton("선택 파일 삭제")
        self.delete_object_button.setProperty("variant", "danger")
        self.delete_object_button.setEnabled(False)
        self.delete_object_button.clicked.connect(self.delete_selected_object)
        file_row.addWidget(self.delete_object_button)
        self.upload = QPushButton("업로드")
        self.upload.setObjectName("s3_upload")
        self.upload.clicked.connect(self.toggle_upload)
        file_row.addWidget(self.upload)
        right.addLayout(file_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        right.addWidget(self.progress)
        self.upload_status = QLabel("")
        self.upload_status.setObjectName("s3_upload_status")
        right.addWidget(self.upload_status)
        layout.addWidget(browser_card, 1)

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

    def set_profile(self, profile_id: int | None) -> None:
        self.cancel_upload()
        self.cancel_download()
        self._profile_id = profile_id
        self.clear_sources()
        self.objects.setRowCount(0)
        self._use_direct_bucket_input()
        self.bucket_catalog.clear()
        if profile_id is None:
            self._set_locations([])
        else:
            self._runner.submit(
                lambda: self._locations.list(profile_id),
                self._set_locations,
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

        def action() -> list[str]:
            return self._s3.list_buckets(profile_id)

        if self._authenticated is None:
            self._runner.submit(action, self._buckets_loaded, self._bucket_catalog_failed)
        else:
            self._authenticated.submit(
                profile_id,
                action,
                self._buckets_loaded,
                self._bucket_catalog_failed,
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
        self.bucket_catalog.setVisible(False)
        self.bucket.setVisible(True)

    def _select_catalog_bucket(self, bucket: str) -> None:
        index = self.bucket_catalog.findText(bucket)
        if index >= 0:
            self.bucket_catalog.setCurrentIndex(index)

    def _catalog_bucket_selected(self, bucket: str) -> None:
        if bucket:
            self.bucket.setText(bucket)
            self._render_breadcrumb()
            if self._profile_id is not None and not self.bucket_catalog.isHidden():
                self.list_objects()

    def _current_bucket(self) -> str:
        if not self.bucket_catalog.isHidden() and self.bucket_catalog.currentText():
            return self.bucket_catalog.currentText()
        return self.bucket.text()

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
        root.setText(bucket)
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
        self.prefix.setText(prefix)
        self._render_breadcrumb()
        self.list_objects()

    def list_objects(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        bucket = self._current_bucket()
        prefix = self.prefix.text()
        self._render_breadcrumb()

        def action() -> list[S3Object]:
            return self._s3.list_objects(bucket, prefix, profile_id)

        if self._authenticated is None:
            self._runner.submit(action, self._objects_loaded, self.error_raised.emit)
        else:
            self._authenticated.submit(
                profile_id,
                action,
                self._objects_loaded,
                self.error_raised.emit,
                self._mfa_cancelled,
            )

    def _objects_loaded(self, value: Any) -> None:
        self._listed_objects = list(value)
        self.objects.setRowCount(len(self._listed_objects))
        for row, item in enumerate(self._listed_objects):
            self.objects.setItem(row, 0, QTableWidgetItem(_object_name(item, self.prefix.text())))
            self.objects.setItem(row, 1, QTableWidgetItem(_object_type(item)))
            self.objects.setItem(
                row,
                2,
                QTableWidgetItem("" if item.is_prefix else str(item.size)),
            )
            self.objects.setItem(
                row,
                3,
                QTableWidgetItem(item.last_modified.isoformat() if item.last_modified else ""),
            )
            self.objects.setRowHeight(row, 46)
        self._sync_object_actions()

    def _sync_object_actions(self) -> None:
        selected = self._selected_objects()
        self.open_object_button.setEnabled(bool(selected) and not self._download_in_progress)
        self.delete_object_button.setEnabled(len(selected) == 1 and not selected[0].is_prefix)

    def _selected_objects(self) -> list[S3Object]:
        rows = sorted(index.row() for index in self.objects.selectionModel().selectedRows())
        return [self._listed_objects[row] for row in rows if 0 <= row < len(self._listed_objects)]

    def download_selected_objects(self) -> None:
        selected = tuple(self._selected_objects())
        if self._profile_id is None or not selected:
            return
        destination_root = self._download_destination(self)
        if destination_root is None:
            return
        profile_id = self._profile_id
        bucket = self._current_bucket()
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
        plan: DownloadPlan = value
        self._download_in_progress = True
        self._sync_object_actions()
        if self._authenticated is None:
            handle = self._runner.submit_cancellable(
                lambda token, progress: self._s3.download(
                    plan, OperationContext(cancellation=token, progress=progress)
                ),
                self._download_completed,
                self._download_failed,
                self._progressed,
            )
        else:
            handle = self._authenticated.submit_long(
                plan.profile_id,
                lambda context: self._s3.download(plan, context),
                self._download_completed,
                self._download_failed,
                self._progressed,
                self._download_cancelled,
            )
        if self._download_in_progress:
            self._download_task = handle

    def _download_completed(self, value: Any) -> None:
        summary: DownloadSummary = value
        self._download_in_progress = False
        self._download_task = None
        self._sync_object_actions()
        self.notice_raised.emit(f"S3 다운로드를 완료했습니다. ({len(summary.downloaded)}개)")
        self._finish_shutdown()

    def _download_failed(self, error: ApplicationError) -> None:
        self._download_in_progress = False
        self._download_task = None
        self._sync_object_actions()
        self.error_raised.emit(error)
        self._finish_shutdown()

    def _download_cancelled(self) -> None:
        self._download_in_progress = False
        self._download_task = None
        self._sync_object_actions()
        self.upload_status.setText("S3 다운로드를 취소했습니다.")
        self.notice_raised.emit("S3 다운로드를 취소했습니다.")
        self._finish_shutdown()

    def _download_mfa_cancelled(self) -> None:
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
        if selected.is_prefix or not self._confirm_delete(self, selected):
            return
        profile_id = self._profile_id
        bucket = self._current_bucket()
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
                self._mfa_cancelled,
            )

    def _object_deleted(self, _value: Any) -> None:
        self.notice_raised.emit("S3 객체를 삭제했습니다.")
        self.list_objects()

    def _object_delete_failed(self, error: ApplicationError) -> None:
        self._sync_object_actions()
        self.error_raised.emit(error)

    def _object_activated(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._listed_objects) and self._listed_objects[row].is_prefix:
            self.prefix.setText(self._listed_objects[row].key)
            self._render_breadcrumb()
            self.list_objects()

    def navigate_up(self) -> None:
        current = self.prefix.text().strip().rstrip("/")
        parent = current.rpartition("/")[0]
        self.prefix.setText(f"{parent}/" if parent else "")
        self._render_breadcrumb()
        self.list_objects()

    def choose_sources(self) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("업로드 대상 추가")
        dialog.setText("파일 또는 폴더를 추가하세요.")
        files = dialog.addButton("파일 추가", QMessageBox.ButtonRole.AcceptRole)
        folder = dialog.addButton("폴더 추가", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() is files:
            paths, _filter = QFileDialog.getOpenFileNames(self, "업로드할 파일 선택")
            self.add_sources(tuple(Path(value) for value in paths))
        elif dialog.clickedButton() is folder:
            path = QFileDialog.getExistingDirectory(self, "업로드할 폴더 선택")
            if path:
                self.add_sources((Path(path),))

    def set_files(self, values: Sequence[Path]) -> None:
        """Compatibility helper for adapters that replace the current file selection."""

        self._selected_files.clear()
        self.add_sources(values)

    def add_sources(self, values: Sequence[Path]) -> None:
        known = {value.expanduser().resolve() for value in self._selected_files}
        for value in values:
            source = Path(value).expanduser().resolve()
            if source in known or not (source.is_file() or source.is_dir()):
                continue
            known.add(source)
            self._selected_files.append(source)
        self._render_sources()

    def remove_selected_sources(self) -> None:
        rows = sorted(
            (self.upload_sources.row(item) for item in self.upload_sources.selectedItems()),
            reverse=True,
        )
        for row in rows:
            if 0 <= row < len(self._selected_files):
                del self._selected_files[row]
        self._render_sources()

    def clear_sources(self) -> None:
        self._selected_files.clear()
        self._render_sources()

    def _render_sources(self) -> None:
        self.upload_sources.clear()
        self.upload_sources.addItems([str(source) for source in self._selected_files])
        count = len(self._selected_files)
        self.file_summary.setText(f"업로드 대상 {count}개" if count else "선택한 파일이 없습니다.")
        self._sync_upload_action()

    def toggle_upload(self) -> None:
        if self._upload_in_progress:
            self.cancel_upload()
        else:
            self.prepare_upload()

    def _sync_upload_action(self) -> None:
        if self._upload_in_progress:
            self.upload.setText("업로드 취소")
            self.upload.setProperty("variant", "danger")
            self.upload.setEnabled(True)
        else:
            self.upload.setText("업로드")
            self.upload.setProperty("variant", "primary")
            self.upload.setEnabled(self._profile_id is not None and bool(self._selected_files))
        self.upload.style().unpolish(self.upload)
        self.upload.style().polish(self.upload)

    def prepare_upload(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        sources = tuple(self._selected_files)
        bucket = self._current_bucket()
        prefix = self.prefix.text()
        self.upload.setEnabled(False)

        def action() -> UploadPlan:
            return self._s3.prepare_upload(sources, bucket, prefix, profile_id)

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
        plan: UploadPlan = value
        policy = self._confirm_upload(self, plan)
        if policy is None:
            self._sync_upload_action()
            return
        self._upload_in_progress = True
        self._sync_upload_action()
        if self._authenticated is None:
            handle = self._runner.submit_cancellable(
                lambda token, progress: self._s3.upload(
                    plan,
                    policy=policy,
                    context=OperationContext(cancellation=token, progress=progress),
                ),
                self._upload_completed,
                self._upload_failed,
                self._progressed,
            )
        else:
            handle = self._authenticated.submit_long(
                plan.profile_id,
                lambda context: self._s3.upload(plan, policy=policy, context=context),
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
        view = build_s3_progress_view_model(event)
        if view.percent is not None:
            self.progress.setValue(view.percent)
        if event.message_code.startswith("s3.download") and event.target:
            self.upload_status.setText(f"다운로드 중: {event.target}")
        else:
            self.upload_status.setText(view.status_text)

    def _upload_completed(self, value: Any) -> None:
        summary: UploadSummary = value
        self._upload_in_progress = False
        self._upload_task = None
        self._sync_upload_action()
        skipped = f", 무시 {len(summary.skipped)}개" if summary.skipped else ""
        self.notice_raised.emit(f"S3 업로드를 완료했습니다. ({len(summary.uploaded)}개{skipped})")
        self.list_objects()
        self._finish_shutdown()

    def _upload_failed(self, error: ApplicationError) -> None:
        self._upload_in_progress = False
        self._upload_task = None
        self._sync_upload_action()
        self.error_raised.emit(error)
        self._finish_shutdown()

    def _mfa_cancelled(self) -> None:
        self._upload_in_progress = False
        self._upload_task = None
        self._sync_upload_action()
        self.upload_status.setText("MFA 인증을 취소했습니다.")
        self._finish_shutdown()

    def _upload_cancelled(self) -> None:
        self._upload_in_progress = False
        self._upload_task = None
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
        if not self._upload_in_progress and not self._download_in_progress:
            completed()
            return
        self._shutdown_callbacks.append(completed)
        self.cancel_upload()
        self.cancel_download()

    def _finish_shutdown(self) -> None:
        if self._upload_in_progress or self._download_in_progress:
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
    existing = [item.uri for item in plan.items if item.exists]
    if not existing:
        return UploadConflictPolicy.SKIP_EXISTING
    representative = next(item.source.name for item in plan.items if item.exists)
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
    skip = dialog.addButton("무시하기", QMessageBox.ButtonRole.AcceptRole)
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
    mime_type, _encoding = mimetypes.guess_type(item.key)
    return mime_type or "파일"
