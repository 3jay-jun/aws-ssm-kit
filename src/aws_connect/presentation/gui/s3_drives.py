"""S3 drive editor and state projection, backed only by shared application services."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.operations import OperationContext
from aws_connect.application.s3_drive_service import S3DriveService
from aws_connect.application.s3_service import S3Service
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.domain.s3_drive import DriveState, DriveStatus, S3Drive
from aws_connect.presentation.gui.authenticated import AuthenticatedGuiRunner, MfaCodeProvider
from aws_connect.presentation.gui.errors import map_error
from aws_connect.presentation.gui.form_fields import form_field
from aws_connect.presentation.gui.icons import gui_icon, set_button_icon
from aws_connect.presentation.gui.list_rows import set_compact_list_row, update_list_row_separators
from aws_connect.presentation.gui.menu_actions import menu_action
from aws_connect.presentation.gui.page_layout import apply_page_layout, page_heading
from aws_connect.presentation.gui.tasks import GuiTaskRunner, TaskHandle

_STATE_TEXT = {
    DriveState.DISCONNECTED: "연결되지 않음",
    DriveState.CONNECTING: "연결 중",
    DriveState.CONNECTED: "연결됨",
    DriveState.DISCONNECTING: "연결 해제 중",
    DriveState.ERROR: "오류",
}


class S3DrivesPage(QWidget):
    error_raised = Signal(object)

    def __init__(
        self,
        service: S3DriveService,
        s3: S3Service | None,
        runner: GuiTaskRunner,
        authenticated: AuthenticatedOperationCoordinator | None,
        mfa: MfaCodeProvider,
    ) -> None:
        super().__init__()
        self._service = service
        self._s3 = s3
        self._runner = runner
        self._authenticated = (
            AuthenticatedGuiRunner(authenticated, runner, self, mfa) if authenticated else None
        )
        self._profile_id: int | None = None
        self._items: dict[int, DriveStatus] = {}
        self._busy = False
        self._loading = False
        self._catalog_loading = False
        self._catalog_attempted: int | None = None
        self._task: TaskHandle | None = None
        self._cancel_requested = False
        self._shutdown: Callable[[], None] | None = None
        self._shutdown_error: Callable[[ApplicationError], None] | None = None
        self._editing_id: int | None = None
        self._dirty = False
        self._filling = False
        self._editor_revision = 0
        self.setObjectName("s3_drives_page")
        layout = QVBoxLayout(self)
        apply_page_layout(layout)
        layout.addLayout(
            page_heading("S3 마운트", "S3 연결을 저장하고 Windows 탐색기에서 사용합니다.")
        )
        content = QHBoxLayout()
        content.setSpacing(16)
        self.session_card = QFrame()
        self.session_card.setObjectName("session_list_card")
        self.session_card.setFixedWidth(280)
        left = QVBoxLayout(self.session_card)
        left.setContentsMargins(8, 8, 8, 8)
        left.setSpacing(8)
        self.add_button = QPushButton("+ 새 연결")
        self.add_button.setProperty("variant", "primary")
        self.add_button.clicked.connect(lambda: self.edit(None))
        self.search = QLineEdit()
        self.search.setPlaceholderText("이름 또는 Bucket 검색")
        self.search.setAccessibleName("저장된 마운트 검색")
        self.search.addAction(
            gui_icon("common-search.svg"), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search.textChanged.connect(self._filter)
        self.table = QListWidget()
        self.table.setObjectName("s3_drive_connections")
        self.table.setProperty("compact", True)
        self.table.currentItemChanged.connect(self._selection_changed)
        self.empty = QLabel("저장된 연결이 없습니다.\n새 연결을 추가해 주세요.")
        self.empty.setObjectName("helper_text")
        self.empty.setWordWrap(True)
        left.addWidget(self.add_button)
        left.addWidget(self.search)
        left.addWidget(self.empty)
        left.addWidget(self.table, 1)
        content.addWidget(self.session_card)
        self.editor_card = QFrame()
        self.editor_card.setObjectName("editor_card")
        editor = QVBoxLayout(self.editor_card)
        editor.setContentsMargins(20, 20, 20, 20)
        editor.setSpacing(16)
        self.editor_title = QLabel("새 연결")
        self.editor_title.setObjectName("section_title")
        self.editor_title.setTextFormat(Qt.TextFormat.PlainText)
        self.editor_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status = QLabel("연결 설정을 저장해 주세요.")
        self.status.setObjectName("helper_text")
        editor.addWidget(self.editor_title)
        editor.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setObjectName("connection_editor_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        fields = QWidget()
        fields.setObjectName("connection_editor_fields")
        form = QVBoxLayout(fields)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(14)
        self.name = QLineEdit()
        self.name.setPlaceholderText("예: 프로젝트 파일")
        self.bucket = QComboBox()
        self.bucket.setEditable(True)
        self.bucket.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.bucket.setMinimumContentsLength(12)
        self.bucket.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.bucket.setPlaceholderText("Bucket 선택 또는 직접 입력")
        self.catalog_button = QPushButton()
        self.catalog_button.setProperty("icon_only", True)
        set_button_icon(self.catalog_button, "common-refresh.svg", tooltip="Bucket 목록 불러오기")
        self.catalog_button.clicked.connect(self._load_buckets)
        bucket_controls = QWidget()
        bucket_row = QHBoxLayout(bucket_controls)
        bucket_row.setContentsMargins(0, 0, 0, 0)
        bucket_row.setSpacing(8)
        bucket_row.addWidget(self.bucket, 1)
        bucket_row.addWidget(self.catalog_button)
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("비워 두면 Bucket 전체")
        self.letter = QComboBox()
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            self.letter.addItem(f"{letter}:", letter)
        self.letter.setCurrentIndex(self.letter.findData("S"))
        self.local = QLineEdit()
        self.local.setPlaceholderText("비워 두면 드라이브 루트")
        self.availability = QLabel("드라이브 문자 확인 중")
        self.availability.setObjectName("helper_text")
        form.addWidget(form_field("마운트 이름", self.name))
        paths = QHBoxLayout()
        paths.setSpacing(14)
        for title, controls in (
            ("S3 위치", (("S3 Bucket", bucket_controls), ("S3 Prefix · 선택", self.prefix))),
            (
                "Windows 위치",
                (("드라이브 문자", self.letter), ("드라이브 내 Prefix · 선택", self.local)),
            ),
        ):
            column = QVBoxLayout()
            column.setSpacing(14)
            heading = QLabel(title)
            heading.setObjectName("section_title")
            column.addWidget(heading)
            for caption, control in controls:
                column.addWidget(form_field(caption, control))
            paths.addLayout(column, 1)
        form.addLayout(paths)
        form.addWidget(self.availability)
        self.preview = QLabel()
        self.preview.setObjectName("notice")
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        mapping = QHBoxLayout()
        mapping.setSpacing(8)
        mapping.addWidget(self.preview, 1)
        self.mapping_status = QLabel()
        self.mapping_status.setObjectName("helper_text")
        mapping.addWidget(self.mapping_status)
        form.addLayout(mapping)
        self.detail = QLabel()
        self.detail.setObjectName("helper_text")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        form.addWidget(self.detail)
        form.addStretch()
        scroll.setWidget(fields)
        editor.addWidget(scroll, 1)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.edit_button = QPushButton("저장")
        self.edit_button.clicked.connect(self.save)
        self.connection_button = QPushButton()
        self.connection_button.setProperty("variant", "primary")
        self.connection_button.clicked.connect(self.toggle_connection)
        self.connect_button = self.connection_button
        self.disconnect_button = self.connection_button
        self.cancel_button = QPushButton("취소")
        self.cancel_button.clicked.connect(self.cancel)
        self.open_button = QPushButton("탐색기에서 열기")
        set_button_icon(self.open_button, "file-folder.svg", tooltip="탐색기에서 열기")
        self.open_button.clicked.connect(self.open_explorer)
        toolbar.addWidget(self.open_button)
        for button, icon, title in (
            (self.edit_button, "common-save.svg", "저장"),
            (self.connection_button, "common-start.svg", "연결 시작"),
        ):
            button.setText("")
            button.setProperty("action_button", True)
            button.setFixedSize(40, 40)
            set_button_icon(
                button,
                icon,
                tooltip=title,
                color="#ffffff" if button is self.connect_button else None,
            )

        toolbar.addStretch()
        for button in (
            self.cancel_button,
            self.edit_button,
            self.connection_button,
        ):
            toolbar.addWidget(button)
        editor.addLayout(toolbar)
        content.addWidget(self.editor_card, 1)
        layout.addLayout(content, 1)
        hint = QLabel(
            "창을 닫아도 연결은 유지됩니다. 트레이에서 완전 종료하면 안전하게 해제합니다."
        )
        hint.setObjectName("helper_text")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        for control in (self.name, self.prefix, self.local):
            control.textChanged.connect(self._changed)
        self.bucket.currentTextChanged.connect(self._changed)
        self.letter.currentIndexChanged.connect(self._changed)
        self._letters: set[str] = set()
        self._update_preview()
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._actions()

    def set_profile(self, profile_id: int | None) -> None:
        if profile_id == self._profile_id:
            self.refresh()
            return
        self._profile_id = profile_id
        self._catalog_attempted = None
        self.bucket.blockSignals(True)
        self.bucket.clear()
        self.bucket.blockSignals(False)
        self._items.clear()
        self.table.blockSignals(True)
        self.table.clear()
        self.table.blockSignals(False)
        self._fill(None)
        self._refresh_letters()
        self._actions()
        self.refresh()
        if self.isVisible():
            self._ensure_buckets()

    def refresh(self) -> None:
        if self._loading or self._shutdown is not None:
            return
        profile_id = self._profile_id
        if profile_id is None:
            return
        self._loading = True

        def loaded(value: list[DriveStatus]) -> None:
            self._loading = False
            if profile_id != self._profile_id:
                self.refresh()
                return
            self._items = {item.drive.require_id(): item for item in value}
            if (
                self._editing_id is not None
                and self._editing_id not in self._items
                and not self._dirty
            ):
                self._fill(None)
            self.table.blockSignals(True)
            self.table.clear()
            for item in value:
                drive_id = item.drive.require_id()
                row = QListWidgetItem()
                row.setData(Qt.ItemDataRole.UserRole, drive_id)
                self.table.addItem(row)
                more = QPushButton()
                more.setProperty("icon_only", True)
                set_button_icon(more, "common-more.svg", tooltip="마운트 작업")
                more.setEnabled(not self._busy)
                more.clicked.connect(
                    lambda _checked=False, drive_id=drive_id, button=more: self._show_menu(
                        drive_id, button
                    )
                )
                set_compact_list_row(
                    self.table,
                    row,
                    item.drive.name,
                    (
                        f"{item.drive.letter}: · {_STATE_TEXT[item.state]}",
                        item.drive.uri.removeprefix("s3://"),
                    ),
                    trailing=more,
                    connected=item.state is DriveState.CONNECTED,
                    show_status=True,
                    elide=True,
                )
                if self._editing_id == item.drive.require_id():
                    self.table.setCurrentItem(row)
            update_list_row_separators(self.table)
            self.table.blockSignals(False)
            self._filter()
            self._actions()

        def failed(error: ApplicationError) -> None:
            self._loading = False
            self.error_raised.emit(error)

        self._runner.submit(lambda: self._service.list(profile_id), loaded, failed)

    def _selected(self) -> S3Drive | None:
        item = self._items.get(self._editing_id) if self._editing_id is not None else None
        return item.drive if item else None

    def _filter(self) -> None:
        query = self.search.text().strip().casefold()
        visible = 0
        for index in range(self.table.count()):
            row = self.table.item(index)
            drive = self._items[row.data(Qt.ItemDataRole.UserRole)].drive
            matches = query in f"{drive.name} {drive.bucket} {drive.prefix}".casefold()
            row.setHidden(not matches)
            visible += matches
        self.empty.setText(
            "검색 결과가 없습니다."
            if query
            else "저장된 연결이 없습니다.\n새 연결을 추가해 주세요."
        )
        self.empty.setVisible(visible == 0)

    def _discard(self) -> bool:
        return (
            not self._dirty
            or QMessageBox.question(
                self,
                "미저장 변경사항",
                "입력한 내용을 버리고 이동할까요?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Discard
        )

    def _selection_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        if self._busy or not self._discard():
            self.table.blockSignals(True)
            if previous is not None:
                self.table.setCurrentItem(previous)
            else:
                self.table.setCurrentRow(-1)
            self.table.blockSignals(False)
            return
        self._fill(self._items[current.data(Qt.ItemDataRole.UserRole)].drive)

    def _fill(self, drive: S3Drive | None) -> None:
        self._filling = True
        self._editor_revision += 1
        self._editing_id = drive.id if drive else None
        self.name.setText(drive.name if drive else "")
        self.bucket.setEditText(drive.bucket if drive else "")
        self.prefix.setText(drive.prefix if drive else "")
        self.local.setText(drive.local_prefix if drive else "")
        self.letter.setCurrentIndex(self.letter.findData(drive.letter if drive else "S"))
        self.editor_title.setText(drive.name if drive else "새 연결")
        self._filling = False
        self._dirty = False
        self._update_preview()
        self._actions()

    def _changed(self) -> None:
        if not self._filling:
            self._dirty = True
            self._editor_revision += 1
        self._update_preview()
        self._actions()

    def _update_preview(self) -> None:
        bucket = self.bucket.currentText().strip()
        prefix = self.prefix.text().strip().strip("/")
        letter = str(self.letter.currentData())
        local = self.local.text().replace("/", "\\").strip("\\")
        self.preview.setText(
            f"s3://{bucket or 'Bucket'}/{prefix + '/' if prefix else ''} → {letter}:\\{local}"
        )
        self.preview.setToolTip(self.preview.text())
        self.availability.setText(
            f"{letter}: · "
            + (
                "사용 가능"
                if letter in getattr(self, "_letters", set())
                else "사용 중 또는 확인 필요"
            )
        )

    def _refresh_letters(self) -> None:
        def loaded(letters: set[str]) -> None:
            self._letters = letters
            self._update_preview()

        self._runner.submit(self._service.available_letters, loaded, self.error_raised.emit)

    def _actions(self) -> None:
        selected = self._selected()
        enabled = not self._busy and self._shutdown is None and self._profile_id is not None
        state = self._items[selected.require_id()].state if selected else DriveState.DISCONNECTED
        editable = enabled and state is DriveState.DISCONNECTED
        self.add_button.setEnabled(enabled)
        self.table.setEnabled(not self._busy)
        for control in (
            self.name,
            self.bucket,
            self.prefix,
            self.local,
            self.letter,
            self.catalog_button,
        ):
            control.setEnabled(editable)
        self.edit_button.setEnabled(editable and self._dirty)
        stopping = self._task is not None or state is not DriveState.DISCONNECTED
        set_button_icon(
            self.connection_button,
            "common-stop.svg" if stopping else "common-start.svg",
            tooltip="연결 취소"
            if self._task is not None
            else "연결 종료"
            if stopping
            else "연결 시작",
            color="#ffffff",
        )
        self.connection_button.setProperty("variant", "danger" if stopping else "primary")
        self.connection_button.style().unpolish(self.connection_button)
        self.connection_button.style().polish(self.connection_button)
        self.connection_button.setEnabled(
            self._shutdown is None
            and not self._cancel_requested
            and (
                self._task is not None
                or (
                    enabled
                    and (
                        selected is not None
                        if stopping
                        else bool(self.name.text().strip() and self.bucket.currentText().strip())
                    )
                )
            )
        )
        self.open_button.setEnabled(enabled and state is DriveState.CONNECTED)
        self.mapping_status.setText(_STATE_TEXT[state])
        self.cancel_button.setVisible(not self._busy and self._dirty)
        self.cancel_button.setEnabled(enabled and self._dirty)
        self.status.setText(
            "연결 취소 중…"
            if self._cancel_requested
            else "작업 중…"
            if self._busy
            else "저장하지 않은 변경사항"
            if self._dirty
            else _STATE_TEXT[state]
            if selected
            else "연결 정보를 입력한 뒤 저장하거나 연결하세요."
        )
        code = self._items[selected.require_id()].message_code if selected else ""
        self.detail.setText(
            map_error(ApplicationError(code, "")).message
            if code
            else "연결된 동안 설정을 변경하려면 먼저 연결을 해제하세요."
            if state is DriveState.CONNECTED
            else "Bucket 목록을 조회할 수 없어도 이름을 직접 입력할 수 있습니다."
        )

    def edit(self, drive: S3Drive | None) -> None:
        if self._profile_id is None or self._busy or not self._discard():
            return
        self.table.blockSignals(True)
        self.table.setCurrentRow(-1)
        self.table.blockSignals(False)
        self._fill(drive)
        self._refresh_letters()
        self.name.setFocus()

    def save(self, *, connect_after: bool = False) -> None:
        if self._profile_id is None:
            return
        try:
            value = S3Drive(
                self._editing_id,
                self._profile_id,
                self.name.text(),
                self.bucket.currentText(),
                self.prefix.text(),
                str(self.letter.currentData()),
                self.local.text(),
            )
        except ApplicationError as error:
            self.error_raised.emit(error)
            return
        profile_id = self._profile_id
        self._busy = True
        self._actions()

        def saved(drive: S3Drive) -> None:
            closing = self._shutdown is not None
            if profile_id == self._profile_id:
                self._items[drive.require_id()] = DriveStatus(drive, DriveState.DISCONNECTED)
                self._fill(drive)
            self._done(None)
            if connect_after and profile_id == self._profile_id and not closing:
                self._connect_drive(drive)

        self._runner.submit(lambda: self._service.save(value), saved, self._failed)

    def _load_buckets(self, *, refresh: bool = True) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        if self._catalog_loading:
            return
        self._catalog_loading = True

        def loaded(buckets: list[str]) -> None:
            self._catalog_loading = False
            if profile_id != self._profile_id:
                self._ensure_buckets()
                return
            self._set_buckets(buckets)

        self._catalog(profile_id, loaded, refresh=refresh)

    def _set_buckets(self, buckets: list[str]) -> None:
        text = self.bucket.currentText()
        self.bucket.blockSignals(True)
        self.bucket.clear()
        self.bucket.addItems(buckets)
        self.bucket.setEditText(text)
        self.bucket.blockSignals(False)

    def _catalog(
        self, profile_id: int, callback: Callable[[Any], None], *, refresh: bool = True
    ) -> None:
        if self._s3 is None:
            self._catalog_loading = False
            return
        s3 = self._s3

        def failed(error: ApplicationError) -> None:
            self._catalog_loading = False
            self.error_raised.emit(error)

        def cancelled() -> None:
            self._catalog_loading = False

        if self._authenticated:
            self._authenticated.submit(
                profile_id,
                lambda: s3.list_buckets(profile_id, refresh=refresh),
                callback,
                failed,
                on_cancel=cancelled,
            )
        else:
            self._runner.submit(
                lambda: s3.list_buckets(profile_id, refresh=refresh), callback, failed
            )

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._ensure_buckets()

    def _ensure_buckets(self) -> None:
        if self._profile_id is None or self._s3 is None or self._catalog_loading:
            return
        profile_id, s3 = self._profile_id, self._s3

        def loaded(cached: list[str] | None) -> None:
            if profile_id != self._profile_id:
                return
            if cached is not None:
                self._set_buckets(cached)
            elif self._catalog_attempted != profile_id:
                self._catalog_attempted = profile_id
                self._load_buckets(refresh=False)

        self._runner.submit(lambda: s3.cached_buckets(profile_id), loaded, self.error_raised.emit)

    def open_explorer(self) -> None:
        drive = self._selected()
        if drive is None:
            return

        def opened(path: str) -> None:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                self.error_raised.emit(
                    ConfigurationError("drive.explorer.failed", "Could not open Explorer")
                )

        self._runner.submit(
            lambda: self._service.connected_path(drive.require_id()), opened, self.error_raised.emit
        )

    def _show_menu(self, drive_id: int, button: QPushButton) -> None:
        if self._busy or self._shutdown is not None:
            return
        item = self._items.get(drive_id)
        if item is None:
            return
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        rename = menu_action(menu, "이름 수정", "common-edit.svg")
        rename.setEnabled(item.state is DriveState.DISCONNECTED)
        rename.triggered.connect(lambda: self._named_action(item.drive, clone=False))
        clone = menu_action(menu, "마운트 복제", "common-copy.svg")
        clone.triggered.connect(lambda: self._named_action(item.drive, clone=True))
        menu.addSeparator()
        delete = menu_action(menu, "마운트 삭제", "common-delete.svg", danger=True)
        delete.triggered.connect(lambda: self.delete_selected(item.drive))
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _named_action(self, drive: S3Drive, *, clone: bool) -> None:
        if not self._discard():
            return
        name, accepted = QInputDialog.getText(
            self,
            "마운트 복제" if clone else "이름 수정",
            "마운트 이름",
            text=drive.name + " 복사본" if clone else drive.name,
        )
        if not accepted:
            return
        profile_id = self._profile_id
        operation = self._service.clone if clone else self._service.rename
        self._busy = True
        self._actions()

        def saved(value: S3Drive) -> None:
            if profile_id == self._profile_id:
                self._items[value.require_id()] = DriveStatus(value, DriveState.DISCONNECTED)
                self._fill(value)
            self._done(None)

        self._runner.submit(lambda: operation(drive.require_id(), name), saved, self._failed)

    def _run(self, operation: Callable[[], object]) -> None:
        self._busy = True
        self._actions()
        self._runner.submit(operation, self._done, self._failed)

    def connect_selected(self) -> None:
        drive = self._selected()
        if drive is None or self._dirty:
            self.save(connect_after=True)
            return
        self._connect_drive(drive)

    def toggle_connection(self) -> None:
        """Keep start, cooperative cancellation and safe disconnect in one place."""
        if self._shutdown is not None or self._cancel_requested:
            return
        if self._task is not None:
            self.cancel()
        elif not self._busy:
            drive = self._selected()
            if (
                drive is not None
                and self._items[drive.require_id()].state is not DriveState.DISCONNECTED
            ):
                self.disconnect_selected()
            else:
                self.connect_selected()

    def _connect_drive(self, drive: S3Drive) -> None:
        self._busy = True
        self._actions()
        if self._authenticated:
            handle = self._authenticated.submit_long(
                drive.profile_id,
                lambda context: self._service.connect(drive.require_id(), context),
                self._done,
                self._failed,
                on_cancel=lambda: self._done(None),
            )
            if self._busy:
                self._task = handle
        else:
            context = OperationContext()
            self._task = TaskHandle(context.cancellation)
            self._runner.submit(
                lambda: self._service.connect(drive.require_id(), context),
                self._done,
                self._failed,
            )
        self._actions()

    def disconnect_selected(self) -> None:
        drive = self._selected()
        if drive is not None:
            self._run(lambda: self._service.disconnect(drive.require_id()))

    def delete_selected(self, drive: S3Drive | None = None) -> None:
        drive = drive or self._selected()
        if (
            drive is not None
            and QMessageBox.question(
                self,
                "마운트 삭제",
                f"{drive.name} 연결을 해제하고 설정을 삭제할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            drive_id = drive.require_id()
            self._busy = True
            self._actions()

            def deleted(_value: object) -> None:
                if self._editing_id == drive_id:
                    self._fill(None)
                self._done(None)

            self._runner.submit(lambda: self._service.delete(drive_id), deleted, self._failed)

    def cancel(self) -> None:
        if self._task and not self._cancel_requested:
            self._cancel_requested = True
            self._task.cancel()
            self._actions()
        elif not self._busy:
            self._fill(self._selected())

    def _done(self, _value: object) -> None:
        self._busy = False
        self._task = None
        self._cancel_requested = False
        self._actions()
        if self._shutdown is not None:
            self._finish_shutdown()
        else:
            self.refresh()
            self._refresh_letters()

    def _failed(self, error: ApplicationError) -> None:
        self._busy = False
        self._task = None
        self.error_raised.emit(error)
        self._done(None)

    def shutdown(
        self, done: Callable[[], None], failed: Callable[[ApplicationError], None]
    ) -> None:
        self._shutdown = done
        self._shutdown_error = failed
        self.cancel()
        self._actions()
        if not self._busy:
            self._finish_shutdown()

    def _finish_shutdown(self) -> None:
        def done(_value: object) -> None:
            callback, self._shutdown = self._shutdown, None
            if callback:
                self._timer.stop()
                callback()

        def failed(error: ApplicationError) -> None:
            callback = self._shutdown_error
            self._shutdown = None
            self._actions()
            if callback:
                callback(error)

        self._runner.submit(self._service.shutdown, done, failed)
