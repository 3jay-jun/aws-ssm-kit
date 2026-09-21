"""Secrets Manager GUI adapter backed by the shared Application Service."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.ec2_service import Ec2Service
from aws_connect.application.ports import ListedSecret
from aws_connect.application.secrets_service import (
    SecretField,
    SecretRelayTarget,
    SecretResult,
    SecretsService,
    build_persistent_remote_secret_command,
)
from aws_connect.domain.errors import ApplicationError, AwsPermissionError
from aws_connect.domain.saved_secret import SavedSecret, SecretLookupMode
from aws_connect.presentation.gui.authenticated import AuthenticatedGuiRunner, MfaCodeProvider
from aws_connect.presentation.gui.clipboard import copy_temporarily
from aws_connect.presentation.gui.icons import gui_icon, icon_text, set_button_icon
from aws_connect.presentation.gui.list_rows import set_compact_list_row, update_list_row_separators
from aws_connect.presentation.gui.page_layout import apply_page_layout, page_heading
from aws_connect.presentation.gui.table_selection import use_first_column_selection_bar
from aws_connect.presentation.gui.tasks import GuiTaskRunner

REVEAL_CLEAR_MILLISECONDS = 30_000


class SecretsPage(QWidget):
    """Direct Secret lookup with explicit per-field reveal/copy actions."""

    error_raised = Signal(object)
    notice_raised = Signal(str)
    rds_values_selected = Signal(str, int)

    def __init__(
        self,
        secrets: SecretsService,
        runner: GuiTaskRunner,
        authenticated: AuthenticatedOperationCoordinator | None = None,
        mfa_code_provider: MfaCodeProvider | None = None,
        *,
        ec2: Ec2Service | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("secrets_page")
        self._secrets = secrets
        self._runner = runner
        self._ec2 = ec2
        self._authenticated = (
            AuthenticatedGuiRunner(authenticated, runner, self, mfa_code_provider)
            if authenticated is not None and mfa_code_provider is not None
            else None
        )
        self._lookup_busy = False
        self._relay_test_busy = False
        self._relay_blocked = False
        self._relay_revision = 0
        self._raw_revealed = False
        self._profile_id: int | None = None
        self._result: SecretResult | None = None
        self._result_generation = 0
        self._revealed_paths: set[str] = set()
        self._catalog_entries: list[ListedSecret] = []
        self._saved_entries: list[SavedSecret] = []
        self._relay_targets: list[SecretRelayTarget] = []
        self._relays_loaded_once = False
        self._pending_relay_instance_id: str | None = None
        self._restored_identifier: str | None = None
        self._pending_saved_id: int | None = None
        self._pending_saved_identifier: str | None = None
        self._hydrate_pending_selection = False
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page_scroll = QScrollArea()
        page_scroll.setObjectName("secret_page_scroll")
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        page_content = QWidget()
        page_content.setObjectName("secret_page_content")
        page_scroll.setWidget(page_content)
        outer.addWidget(page_scroll)
        root = QVBoxLayout(page_content)
        apply_page_layout(root)
        page_head = QHBoxLayout()
        heading_copy = page_heading(
            "Secrets Manager", "현재 조회 방식으로 접근 가능한 Secrets Manager 항목을 표시합니다."
        )
        page_head.addLayout(heading_copy)
        page_head.addStretch()
        self.list_button = QPushButton()
        self.list_button.setProperty("action_button", True)
        self.list_button.setObjectName("secret_list")
        set_button_icon(self.list_button, "common-refresh.svg", tooltip="목록 새로고침")
        self.list_button.setEnabled(False)
        self.list_button.clicked.connect(self.load_list)
        page_head.addWidget(self.list_button, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(page_head)
        root.addSpacing(8)

        toolbar = QFrame()
        toolbar.setObjectName("secret_toolbar")
        query = QHBoxLayout(toolbar)
        query.setContentsMargins(0, 0, 0, 0)
        mode_group = QVBoxLayout()
        mode_group.setSpacing(6)
        mode_label = QLabel("조회 방식")
        mode_label.setObjectName("field_label")
        mode_group.addWidget(mode_label)
        self.lookup_mode = QComboBox()
        self.lookup_mode.setObjectName("secret_lookup_mode")
        self.lookup_mode.addItem("직접 조회", SecretLookupMode.DIRECT)
        self.lookup_mode.addItem("EC2 경유 조회", SecretLookupMode.VIA_EC2)
        mode_group.addWidget(self.lookup_mode)
        query.addLayout(mode_group)
        id_group = QVBoxLayout()
        id_group.setSpacing(6)
        id_label = QLabel("Secret 검색")
        id_label.setObjectName("field_label")
        id_group.addWidget(id_label)
        self.secret_id = QLineEdit()
        self.secret_id.setObjectName("secret_id")
        self.secret_id.setPlaceholderText("이름 또는 Secret ID로 검색 (예: brandbay_rds_prod)")
        self.secret_id.addAction(
            gui_icon("common-search.svg"), QLineEdit.ActionPosition.LeadingPosition
        )
        self.secret_id.textChanged.connect(self._update_actions)
        id_group.addWidget(self.secret_id)
        self.secret_selector = QComboBox()
        self.secret_selector.setObjectName("secret_selector")
        self.secret_selector.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.secret_selector.setMinimumContentsLength(12)
        self.secret_selector.setEditable(True)
        self.secret_selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        selector_editor = self.secret_selector.lineEdit()
        if selector_editor is None:
            raise RuntimeError("Secret 검색 입력창을 초기화하지 못했습니다.")
        selector_editor.setPlaceholderText(self.secret_id.placeholderText())
        selector_editor.addAction(
            gui_icon("common-search.svg"), QLineEdit.ActionPosition.LeadingPosition
        )
        selector_editor.textChanged.connect(self._update_actions)
        selector_editor.returnPressed.connect(self.get_secret)
        self.secret_selector.currentIndexChanged.connect(self._selector_changed)
        self.secret_selector.hide()
        id_group.addWidget(self.secret_selector)
        self.catalog_status = QLabel("Secret 목록 권한을 확인하는 중입니다.")
        self.catalog_status.setObjectName("helper_text")
        self.catalog_status.setWordWrap(True)
        query.addLayout(id_group, 1)
        self.get_button = QPushButton()
        self.get_button.setProperty("action_button", True)
        self.get_button.setObjectName("secret_get")
        self.get_button.setProperty("variant", "primary")
        set_button_icon(self.get_button, "common-search.svg", tooltip="조회", color="#ffffff")
        self.get_button.setEnabled(False)
        self.get_button.clicked.connect(self.get_secret)
        self.secret_id.returnPressed.connect(self.get_secret)
        query.addWidget(self.get_button, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addWidget(toolbar)
        root.addWidget(self.catalog_status)

        relay_panel = QFrame()
        relay_panel.setObjectName("secret_relay_panel")
        relay_controls = QHBoxLayout(relay_panel)
        self.relay_instance = QComboBox()
        self.relay_instance.setObjectName("secret_relay_instance")
        self.relay_instance.currentIndexChanged.connect(self._relay_changed)
        relay_controls.addWidget(QLabel("중계 EC2"))
        self.relay_instance.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.relay_instance.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        relay_controls.setContentsMargins(0, 0, 0, 0)
        relay_controls.setSpacing(14)
        relay_controls.addWidget(self.relay_instance, 1)
        self.relay_status = QLabel("선택 필요")
        self.relay_status.setObjectName("secret_relay_status")
        relay_controls.addWidget(self.relay_status)
        self.test_relay_button = QPushButton("연결 테스트")
        set_button_icon(self.test_relay_button, "common-refresh.svg")
        self.test_relay_button.clicked.connect(self.test_relay_connection)
        relay_controls.addWidget(self.test_relay_button)
        root.addWidget(relay_panel)
        self.relay_panel = relay_panel
        self.relay_consent = QCheckBox(
            "선택한 EC2를 통해 Secrets Manager를 조회하는 데 동의합니다."
        )
        self.relay_consent.setObjectName("secret_relay_consent")
        self.relay_consent.toggled.connect(self._update_actions)
        root.addWidget(self.relay_consent)
        self.relay_notice = icon_text(
            "common-info.svg",
            "선택한 EC2 인스턴스에 SSM으로 연결하여 Secrets Manager를 조회합니다.\n"
            "EC2의 IAM 권한에 따라 조회 가능한 Secret이 달라집니다. "
            "SSM command output에 원문이 일시 존재할 수 있습니다.",
            color="#2563eb",
        )
        self.relay_notice.setObjectName("secret_relay_notice")
        relay_note = self.relay_notice.findChild(QLabel, "icon_text_label")
        if relay_note is None:
            raise RuntimeError("EC2 경유 조회 안내를 초기화하지 못했습니다.")
        relay_note.setWordWrap(True)
        relay_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        note_layout = self.relay_notice.layout()
        if not isinstance(note_layout, QHBoxLayout):
            raise RuntimeError("EC2 경유 조회 안내 레이아웃을 초기화하지 못했습니다.")
        note_layout.setContentsMargins(14, 10, 14, 10)
        note_layout.setStretch(1, 1)
        root.addWidget(self.relay_notice)
        self.terminal_fallback_button = QPushButton("EC2 직접 조회")
        self.terminal_fallback_button.setObjectName("secret_terminal_fallback")
        set_button_icon(self.terminal_fallback_button, "common-start.svg")
        self.terminal_fallback_button.clicked.connect(self.open_terminal_fallback)
        self.terminal_fallback_button.hide()
        root.addWidget(self.terminal_fallback_button, alignment=Qt.AlignmentFlag.AlignLeft)
        content = QHBoxLayout()
        content.setSpacing(16)
        catalog_card = QFrame()
        catalog_card.setObjectName("secret_catalog_card")
        catalog_card.setProperty("role", "card")
        self.catalog_card = catalog_card
        catalog_card.setFixedWidth(360)
        catalog = QVBoxLayout(catalog_card)
        catalog.setContentsMargins(20, 20, 20, 20)
        self.register_saved_button = QPushButton("+ 수동 생성")
        self.register_saved_button.setProperty("variant", "primary")
        self.register_saved_button.setToolTip("로컬 SQLite 저장 항목 생성")
        self.register_saved_button.clicked.connect(self.register_saved)
        catalog.addWidget(self.register_saved_button)
        self.saved_status = QLabel("프로필을 선택하면 저장 항목을 불러옵니다.")
        self.saved_status.setObjectName("helper_text")
        catalog.addWidget(self.saved_status)
        self.catalog = QListWidget()
        self.catalog.setObjectName("secret_catalog")
        self.catalog.itemClicked.connect(self._catalog_selected)
        self.catalog.itemDoubleClicked.connect(lambda _item: self.edit_saved())
        self.catalog.currentRowChanged.connect(lambda _row: self._selection_changed())
        catalog.addWidget(self.catalog, 1)
        self.delete_saved_button = QPushButton(self)
        self.delete_saved_button.hide()
        self.delete_saved_button.clicked.connect(self.delete_saved)
        content.addWidget(catalog_card)
        result_card = QFrame()
        result_card.setObjectName("secret_result_card")
        result_card.setProperty("role", "card")
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(20, 20, 20, 20)
        result_head = QHBoxLayout()
        self.summary = QLabel("조회한 Secret이 없습니다.")
        self.summary.setObjectName("secret_summary")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        detail_icon = QLabel()
        detail_icon.setPixmap(gui_icon("tab-secrets.svg").pixmap(24, 24))
        result_head.addWidget(detail_icon)
        result_head.addWidget(self.summary, 1)
        self.last_retrieved = QLabel("마지막 조회 -")
        self.last_retrieved.setObjectName("helper_text")
        result_head.addWidget(self.last_retrieved)
        result_layout.addLayout(result_head)
        self.hide_values_button = QPushButton("값 숨기기")
        set_button_icon(self.hide_values_button, "common-eye.svg")
        self.hide_values_button.clicked.connect(self.hide_values)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("secret_detail_tabs")
        self.tabs.setCornerWidget(self.hide_values_button)
        self.fields = QTableWidget(0, 3)
        self.fields.setObjectName("secret_fields")
        self.fields.setHorizontalHeaderLabels(["키", "값", "액션"])
        self.fields.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.fields.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.fields.verticalHeader().hide()
        self.fields.setShowGrid(False)
        self.fields.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.fields.setColumnWidth(0, 120)
        self.fields.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.fields.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.fields.setColumnWidth(2, 88)
        self.fields.verticalHeader().setDefaultSectionSize(38)
        self.fields.itemSelectionChanged.connect(self._selection_changed)
        self.fields.cellDoubleClicked.connect(lambda _row, _column: self.reveal_selected())
        use_first_column_selection_bar(self.fields)
        self.tabs.addTab(self.fields, "키-값 보기")
        raw_page = QWidget()
        raw_layout = QVBoxLayout(raw_page)
        self.reveal_raw_button = QPushButton("원문 일시 표시 (30초)")
        self.reveal_raw_button.clicked.connect(self.reveal_raw)
        raw_layout.addWidget(self.reveal_raw_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        raw_layout.addWidget(self.raw_view)
        self.tabs.addTab(raw_page, "원문 보기 (JSON)")
        result_layout.addWidget(self.tabs, 1)
        notice = QLabel(
            "조회한 값과 조회 컨텍스트는 이 PC의 SQLite에 저장됩니다. "
            "전체 내용 복사로 원문을 복사할 수 있습니다."
        )
        notice.setObjectName("secret_notice")
        notice.setWordWrap(True)
        self.result_notice = notice

        self.copy_all_button = QToolButton()
        self.copy_all_button.setText("전체 내용 복사")
        self.copy_all_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.copy_all_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        copy_menu = QMenu(self.copy_all_button)
        for text, format_name in (
            ("전체 내용 복사", "raw"),
            ("JSON 복사", "json"),
            ("키-값 복사", "key_value"),
        ):
            copy_action = copy_menu.addAction(text)
            copy_action.triggered.connect(
                lambda _checked=False, value=format_name: self.copy_format(value)
            )
        self.copy_all_button.setMenu(copy_menu)
        self.copy_all_button.setObjectName("secret_copy_all")
        set_button_icon(self.copy_all_button, "common-copy.svg")
        self.copy_all_button.clicked.connect(self.copy_all)
        footer = QHBoxLayout()
        footer.addWidget(notice, 1)
        footer.addWidget(self.copy_all_button, alignment=Qt.AlignmentFlag.AlignBottom)
        result_layout.addLayout(footer)
        content.addWidget(result_card, 1)
        root.addLayout(content, 1)
        self._selection_changed()
        self.lookup_mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

    def set_profile(self, profile_id: int | None) -> None:
        if self._profile_id == profile_id:
            return
        self._profile_id = profile_id
        self._lookup_busy = False
        self._relay_test_busy = False
        self._relay_revision += 1
        self._clear_result()
        self.summary.setText("조회한 Secret이 없습니다.")
        self.secret_id.setProperty("source_identifier", None)
        self.secret_id.clear()
        self.relay_instance.clear()
        self._relay_targets = []
        self._relays_loaded_once = False
        self._pending_relay_instance_id = None
        self._restored_identifier = None
        self._pending_saved_id = None
        self._pending_saved_identifier = None
        self._hydrate_pending_selection = False
        self.relay_consent.setChecked(False)
        self.list_button.setEnabled(profile_id is not None)
        self._update_actions()
        self.catalog.clear()
        self.secret_selector.clear()
        self._catalog_entries = []
        self._saved_entries = []
        self._selection_changed()
        if profile_id is not None:
            self.catalog_status.setText("Secret 목록을 불러오는 중입니다…")
            self.saved_status.setText("저장된 Secret을 불러오는 중입니다…")
            self.load_saved()
            self.load_list()
            self.load_relays()

    def load_saved(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        self._runner.submit(
            lambda: self._secrets.list_saved(profile_id),
            lambda value: self._saved_loaded(value) if self._profile_id == profile_id else None,
            lambda error: self.error_raised.emit(error) if self._profile_id == profile_id else None,
        )

    def _saved_loaded(self, value: Any) -> None:
        self._saved_entries = list(value)
        self.catalog.clear()
        for saved in self._saved_entries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, saved)
            self.catalog.addItem(item)
            identifier = _short_secret_id(saved.identifier)
            set_compact_list_row(
                self.catalog,
                item,
                identifier.rsplit("/", 1)[-1],
                (f"Secret ID  {saved.identifier}",),
                title_suffix=(
                    saved.last_retrieved_at.astimezone().strftime("%Y-%m-%d %H:%M")
                    if saved.last_retrieved_at
                    else "-"
                ),
                elide=True,
                trailing=self._saved_menu_button(saved),
            )
        update_list_row_separators(self.catalog)
        self.saved_status.setText("" if self._saved_entries else "저장된 Secret이 없습니다.")
        self.saved_status.setVisible(not self._saved_entries)
        selected_row = next(
            (
                row
                for row, saved in enumerate(self._saved_entries)
                if (self._pending_saved_id is not None and saved.id == self._pending_saved_id)
                or (
                    self._pending_saved_id is None
                    and self._pending_saved_identifier is not None
                    and saved.identifier == self._pending_saved_identifier
                )
            ),
            -1,
        )
        should_hydrate = self._hydrate_pending_selection
        self._pending_saved_id = None
        self._pending_saved_identifier = None
        self._hydrate_pending_selection = False
        if selected_row >= 0:
            self.catalog.setCurrentRow(selected_row)
            if should_hydrate:
                self._load_saved_entry(self._saved_entries[selected_row])
        self._selection_changed()

    def load_list(self) -> None:
        if self.lookup_mode.currentData() == SecretLookupMode.VIA_EC2:
            self.load_saved()
            self.load_relays()
            return
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        self.list_button.setEnabled(False)

        def action() -> list[ListedSecret]:
            return self._secrets.list(profile_id)

        def loaded(value: Any) -> None:
            if self._profile_id == profile_id:
                self._list_loaded(value)

        def failed(error: ApplicationError) -> None:
            if self._profile_id == profile_id:
                self._list_failed(error)

        if self._authenticated is None:
            self._runner.submit(action, loaded, failed)
        else:
            self._authenticated.submit(profile_id, action, loaded, failed, self._cancelled)

    def _list_loaded(self, value: Any) -> None:
        self.list_button.setEnabled(self._profile_id is not None)
        self._catalog_entries = list(value)
        self.catalog_status.setText(
            "" if self._catalog_entries else "조회 권한은 있지만 표시할 Secret이 없습니다."
        )
        self.secret_selector.clear()
        for secret in self._catalog_entries:
            self.secret_selector.addItem(secret.name, secret)
        if self.lookup_mode.currentData() == SecretLookupMode.VIA_EC2:
            self.secret_selector.hide()
            self.secret_id.show()
        elif self._restored_identifier is None:
            self.secret_id.hide()
            self.secret_selector.show()
            self._selector_changed()
        else:
            self._restore_identifier(self._restored_identifier)

    def _selector_changed(self) -> None:
        secret = self.secret_selector.currentData()
        if isinstance(secret, ListedSecret):
            self.secret_id.setProperty("source_identifier", secret.arn)
            self.secret_id.setText(_short_secret_id(secret.arn))
        self._update_actions()

    def _catalog_selected(self, item: QListWidgetItem) -> None:
        secret = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(secret, SavedSecret):
            self._load_saved_entry(secret)

    def _load_saved_entry(self, saved: SavedSecret) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        self._clear_result()
        generation = self._result_generation
        self._runner.submit(
            lambda: self._secrets.load_saved(saved.require_id(), profile_id),
            lambda value: (
                self._saved_snapshot_loaded(value)
                if generation == self._result_generation
                else None
            ),
            lambda error: (
                self.error_raised.emit(error) if generation == self._result_generation else None
            ),
        )

    def _saved_snapshot_loaded(self, value: Any) -> None:
        saved, result = value
        self._result = result
        self._result_generation += 1
        self._revealed_paths.clear()
        self._raw_revealed = False
        self._restored_identifier = saved.identifier
        self._restore_identifier(saved.identifier)
        mode_index = self.lookup_mode.findData(saved.lookup_mode)
        if mode_index >= 0:
            self.lookup_mode.setCurrentIndex(mode_index)
        self._pending_relay_instance_id = saved.relay_instance_id
        if self._relays_loaded_once:
            self._render_relays()
        self.summary.show()
        self._render()

    def _restore_identifier(self, identifier: str) -> None:
        self.secret_id.setProperty("source_identifier", identifier)
        self.secret_id.setText(_short_secret_id(identifier))
        match = next(
            (
                index
                for index, listed in enumerate(self._catalog_entries)
                if identifier in (listed.name, listed.arn)
            ),
            -1,
        )
        if match >= 0 and self.lookup_mode.currentData() == SecretLookupMode.DIRECT:
            self.secret_selector.setCurrentIndex(match)
            self.secret_id.hide()
            self.secret_selector.show()
        else:
            self.secret_selector.hide()
            self.secret_id.show()
        self._update_actions()

    def _list_failed(self, error: ApplicationError) -> None:
        # ListSecrets is optional. A failure must not disable direct GetSecretValue.
        self.list_button.setEnabled(self._profile_id is not None)
        self._update_actions()
        if isinstance(error, AwsPermissionError):
            self._catalog_entries = []
            self.secret_selector.clear()
            self.catalog_status.setText(
                "Secret 목록 조회 권한이 없습니다. 이름 또는 ARN으로 직접 조회할 수 있습니다."
            )
            self.notice_raised.emit("Secret 목록 권한이 없어 이름 또는 ARN 직접 검색을 유지합니다.")
            self.secret_selector.hide()
            self.secret_id.show()
            return
        self.error_raised.emit(error)

    def get_secret(self) -> None:
        self._request_secret(self._current_identifier())

    def _current_identifier(self) -> str:
        secret = self.secret_selector.currentData()
        if not self.secret_selector.isHidden():
            text = self.secret_selector.currentText().strip()
            return secret.arn if isinstance(secret, ListedSecret) and text == secret.name else text
        text = self.secret_id.text().strip()
        source = self.secret_id.property("source_identifier")
        return source if isinstance(source, str) and _short_secret_id(source) == text else text

    def _request_secret(self, identifier: str) -> None:
        if self._profile_id is None or self._lookup_busy:
            return
        profile_id = self._profile_id
        self._restored_identifier = None
        self.secret_id.setProperty("source_identifier", identifier)
        self.secret_id.setText(_short_secret_id(identifier))
        mode = self.lookup_mode.currentData()
        relay = self.relay_instance.currentData()
        if mode == SecretLookupMode.VIA_EC2 and (
            not isinstance(relay, SecretRelayTarget)
            or not self.relay_consent.isChecked()
            or self._relay_blocked
            or self._relay_test_busy
        ):
            return
        self._clear_result()
        self._lookup_busy = True
        self.get_button.setEnabled(False)
        self.summary.setText("조회 중…")
        self.relay_consent.setChecked(False)

        def action() -> SecretResult:
            if mode == SecretLookupMode.DIRECT:
                return self._secrets.get(identifier, profile_id)
            return self._secrets.get(
                identifier,
                profile_id,
                mode=SecretLookupMode.VIA_EC2,
                instance_id=relay.instance_id,
                confirmed=True,
            )

        generation = self._result_generation

        def loaded(value: SecretResult) -> None:
            if generation == self._result_generation:
                self._loaded(value)

        def failed(error: ApplicationError) -> None:
            if generation == self._result_generation:
                self._failed(error)

        def cancelled() -> None:
            if generation == self._result_generation:
                self._cancelled()

        if self._authenticated is None:
            self._runner.submit(action, loaded, failed)
        else:
            self._authenticated.submit(profile_id, action, loaded, failed, cancelled)

    def _cancelled(self) -> None:
        if self._relay_test_busy:
            self.relay_status.setText("연결 확인 취소")
        self._lookup_busy = False
        self._relay_test_busy = False
        self._update_actions()
        self.list_button.setEnabled(self._profile_id is not None)
        self.summary.setText("MFA 인증을 취소했습니다.")

    def _loaded(self, value: Any) -> None:
        self._lookup_busy = False
        self._result = value
        self._result_generation += 1
        self._revealed_paths.clear()
        self._update_actions()
        self.summary.show()
        self._render()
        self._pending_saved_identifier = self._result.secret_id
        self.load_saved()

    def _render(self) -> None:
        result = self._result
        self._clear_fields()
        self.fields.setRowCount(0 if result is None else len(result.fields))
        if result is None:
            self._selection_changed()
            return
        identifier = _short_secret_id(result.secret_id)
        self.summary.setText(f"{identifier.rsplit('/', 1)[-1]}\nSecret ID  {identifier}")
        self.last_retrieved.setText(
            f"마지막 조회 {result.retrieved_at.astimezone():%Y-%m-%d %H:%M:%S}"
            if result.retrieved_at is not None
            else "마지막 조회 -"
        )
        self.raw_view.setPlainText(result.json_text(reveal=self._raw_revealed))
        for row, field in enumerate(result.fields):
            self.fields.setItem(row, 0, QTableWidgetItem(field.path))
            value = field.reveal() if field.path in self._revealed_paths else field.masked_value
            self.fields.setItem(row, 1, QTableWidgetItem(_display(value)))
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)
            action_layout.addStretch()
            if field.sensitive:
                reveal = QPushButton()
                reveal.setProperty("icon_only", True)
                set_button_icon(reveal, "common-eye.svg", tooltip="값 일시 표시/숨기기")
                reveal.clicked.connect(lambda _checked=False, index=row: self._reveal_row(index))
                action_layout.addWidget(reveal)
            copy = QPushButton()
            copy.setProperty("icon_only", True)
            set_button_icon(copy, "common-copy.svg", tooltip="값 복사")
            copy.clicked.connect(lambda _checked=False, index=row: self.copy_field(index))
            action_layout.addWidget(copy)
            self.fields.setCellWidget(row, 2, actions)
        first_editable = next(
            (
                row
                for row, field in enumerate(result.fields)
                if result.kind.value == "json" and "." not in field.path and "[" not in field.path
            ),
            -1,
        )
        if first_editable >= 0:
            self.fields.selectRow(first_editable)
        self._selection_changed()

    def reveal_selected(self) -> None:
        field = self._selected_field()
        if field is None:
            return
        if field.path in self._revealed_paths:
            self._revealed_paths.remove(field.path)
        else:
            self._revealed_paths.add(field.path)
            expected_generation = self._result_generation
            QTimer.singleShot(
                REVEAL_CLEAR_MILLISECONDS,
                lambda: self._hide_revealed(field.path, expected_generation),
            )
        self._render()
        result = self._result
        if result is None:
            return
        row = next(i for i, item in enumerate(result.fields) if item is field)
        self.fields.selectRow(row)

    def _hide_revealed(self, path: str, generation: int) -> None:
        if self._result_generation == generation and path in self._revealed_paths:
            self._revealed_paths.remove(path)
            self._render()

    def _clear_result(self) -> None:
        self.summary.show()
        self._result_generation += 1
        self._result = None
        self._lookup_busy = False
        self._raw_revealed = False
        self._revealed_paths.clear()
        self.raw_view.clear()
        self.last_retrieved.setText("마지막 조회 -")
        self.tabs.setCurrentIndex(0)
        self._clear_fields()
        self.terminal_fallback_button.hide()
        self._selection_changed()

    def _clear_fields(self) -> None:
        for row in range(self.fields.rowCount()):
            widget = self.fields.cellWidget(row, 2)
            if widget is not None:
                widget.hide()
        self.fields.setRowCount(0)

    def copy_all(self) -> None:
        if self._result is None:
            return
        self.copy_format("raw")

    def copy_format(self, format_name: str) -> None:
        if self._result is not None:
            copy_temporarily(self._result.copy_text(format_name))
            self.notice_raised.emit("복사했습니다. 30초 후 클립보드에서 제거합니다.")

    def copy_field(self, row: int) -> None:
        if self._result is not None and 0 <= row < len(self._result.fields):
            copy_temporarily(_display(self._result.fields[row].reveal()))
            self.notice_raised.emit("값을 복사했습니다. 30초 후 클립보드에서 제거합니다.")

    def _reveal_row(self, row: int) -> None:
        self.fields.selectRow(row)
        self.reveal_selected()

    def reveal_raw(self) -> None:
        if self._result is None:
            return
        self._raw_revealed = True
        generation = self._result_generation
        QTimer.singleShot(REVEAL_CLEAR_MILLISECONDS, lambda: self._hide_raw(generation))
        self._render()

    def _hide_raw(self, generation: int) -> None:
        if generation == self._result_generation:
            self._raw_revealed = False
            self._render()

    def hide_values(self) -> None:
        self._revealed_paths.clear()
        self._raw_revealed = False
        self._render()

    def _saved_menu_button(self, saved: SavedSecret) -> QPushButton:
        button = QPushButton()
        button.setProperty("icon_only", True)
        button.setFixedSize(24, 24)
        set_button_icon(button, "common-more.svg", tooltip="저장된 Secret 더보기")
        button.clicked.connect(lambda: self._show_saved_menu(saved, button))
        return button

    def _show_saved_menu(self, saved: SavedSecret, button: QPushButton) -> None:
        row = next((i for i, value in enumerate(self._saved_entries) if value.id == saved.id), -1)
        if row < 0:
            return
        self.catalog.setCurrentRow(row)
        menu = QMenu(button)
        menu.setObjectName("secret_saved_menu")
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        edit = menu.addAction("수정하기")
        edit.triggered.connect(self.edit_saved)
        for text, value in (
            ("Secret ID 복사", saved.identifier),
            ("이름 복사", _short_secret_id(saved.identifier).rsplit("/", 1)[-1]),
        ):
            action = menu.addAction(gui_icon("common-copy.svg"), text)
            action.triggered.connect(
                lambda _checked=False, content=value: copy_temporarily(content)
            )
        delete = QWidgetAction(menu)
        delete.setText("저장된 Secret 삭제")
        delete_button = QPushButton("저장된 Secret 삭제")
        delete_button.setObjectName("secret_menu_delete")
        set_button_icon(delete_button, "common-delete.svg", color="#d92d20")
        delete.setDefaultWidget(delete_button)

        def delete_entry() -> None:
            current_row = next(
                (i for i, value in enumerate(self._saved_entries) if value.id == saved.id), -1
            )
            if current_row >= 0 and self._profile_id == saved.profile_id:
                self.catalog.setCurrentRow(current_row)
                self.delete_saved()

        delete.triggered.connect(delete_entry)
        delete_button.clicked.connect(delete.trigger)
        delete_button.clicked.connect(menu.close)
        menu.addAction(delete)
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _relay_changed(self) -> None:
        self._relay_revision += 1
        self._relay_test_busy = False
        self._relay_blocked = False
        self.relay_consent.setChecked(False)
        available = isinstance(self.relay_instance.currentData(), SecretRelayTarget)
        self.relay_status.setText("● 연결 가능" if available else "중계 EC2 선택 필요")
        self.relay_status.setToolTip("SSM Online 상태 기준이며 조회 시 다시 확인합니다.")
        self._update_actions()

    def test_relay_connection(self) -> None:
        relay = self.relay_instance.currentData()
        if (
            self._profile_id is None
            or not isinstance(relay, SecretRelayTarget)
            or self._relay_test_busy
        ):
            return
        self._relay_revision += 1
        revision, profile_id = self._relay_revision, self._profile_id
        self._relay_test_busy = True
        self.relay_status.setText("확인 중…")
        self._update_actions()

        def completed(_value: SecretRelayTarget) -> None:
            if revision != self._relay_revision:
                return
            self._relay_test_busy = False
            self._relay_blocked = False
            self.relay_status.setText("● 연결 가능")
            self._update_actions()

        def failed(error: ApplicationError) -> None:
            if revision != self._relay_revision:
                return
            self._relay_test_busy = False
            self._relay_blocked = True
            self.relay_status.setText("연결 불가")
            self._update_actions()
            self.error_raised.emit(error)

        def operation() -> SecretRelayTarget:
            return self._secrets.test_relay_connection(relay.instance_id, profile_id)

        if self._authenticated is None:
            self._runner.submit(operation, completed, failed)
        else:
            self._authenticated.submit(profile_id, operation, completed, failed, self._cancelled)

    def copy_to_rds(self) -> None:
        if self._result is None:
            return
        try:
            host, port = self._result.rds_endpoint()
        except ApplicationError as error:
            self.error_raised.emit(error)
            return
        self.rds_values_selected.emit(host, port)

    def _selected_field(self) -> SecretField | None:
        if self._result is None or self.fields.currentRow() < 0:
            return None
        return self._result.fields[self.fields.currentRow()]

    def _selection_changed(self) -> None:
        self.saved_status.setVisible(bool(self.saved_status.text()))
        has_result = self._result is not None
        self.hide_values_button.setEnabled(bool(self._revealed_paths) or self._raw_revealed)
        self.reveal_raw_button.setEnabled(has_result and not self._raw_revealed)
        saved_selected = 0 <= self.catalog.currentRow() < len(self._saved_entries)
        self.delete_saved_button.setEnabled(saved_selected)
        self.register_saved_button.setEnabled(self._profile_id is not None)
        self.copy_all_button.setEnabled(has_result)
        self.copy_all_button.setVisible(has_result)
        self.result_notice.setVisible(has_result)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        """Keep the approved desktop codebox while avoiding overlap at 720p."""

        self.fields.setMinimumHeight(66 if self.height() < 650 else 150)
        self.catalog_card.setFixedWidth(360 if self.width() < 1000 else 420)
        super().resizeEvent(event)

    def _failed(self, error: ApplicationError) -> None:
        self._lookup_busy = False
        self._update_actions()
        self.summary.setText("조회 실패 · 앱은 계속 실행됩니다.")
        self.terminal_fallback_button.setVisible(
            isinstance(error, AwsPermissionError)
            and error.aws_service == "ssm"
            and error.aws_action == "SendCommand"
            and self._ec2 is not None
        )
        self.error_raised.emit(error)

    def open_terminal_fallback(self) -> None:
        """Open a durable terminal, run the fixed lookup, then keep its shell open."""

        relay = self.relay_instance.currentData()
        ec2 = self._ec2
        if self._profile_id is None or ec2 is None or not isinstance(relay, SecretRelayTarget):
            return
        try:
            command = build_persistent_remote_secret_command(
                self._current_identifier(), relay.platform_name
            )
        except ApplicationError as error:
            self.error_raised.emit(error)
            return
        profile_id = self._profile_id
        self.terminal_fallback_button.setEnabled(False)

        def action() -> object:
            return ec2.connect_external(
                relay.instance_id,
                profile_id,
                document_name="AWS-StartInteractiveCommand",
                parameters={"command": [command]},
            )

        self._runner.submit(action, self._terminal_opened, self._terminal_failed)

    def _terminal_opened(self, _value: Any) -> None:
        self.terminal_fallback_button.setEnabled(True)
        self.notice_raised.emit(
            "EC2 터미널에서 Secret 조회 명령을 실행했습니다. 터미널은 계속 유지됩니다."
        )

    def register_saved(self) -> None:
        if self._profile_id is None:
            return
        edited = _prompt_saved_secret(self, None)
        if edited is None:
            return
        identifier, value = edited
        profile_id = self._profile_id
        self._runner.submit(
            lambda: self._secrets.remember(identifier, profile_id, value=value),
            lambda saved: self._saved_changed(
                "Secret을 등록했습니다.", saved.require_id(), hydrate=True
            ),
            self.error_raised.emit,
        )

    def edit_saved(self) -> None:
        row = self.catalog.currentRow()
        if self._profile_id is None or not (0 <= row < len(self._saved_entries)):
            return
        selected = self._saved_entries[row]
        edited = _prompt_saved_secret(self, selected)
        if edited is None:
            return
        identifier, value = edited
        self._runner.submit(
            lambda: self._secrets.update_saved(
                selected.require_id(), identifier, self._profile_id, value=value
            ),
            lambda updated: self._saved_changed(
                "저장된 Secret을 수정했습니다.", updated.require_id(), hydrate=True
            ),
            self.error_raised.emit,
        )

    def delete_saved(self) -> None:
        row = self.catalog.currentRow()
        if self._profile_id is None or not (0 <= row < len(self._saved_entries)):
            return
        selected = self._saved_entries[row]
        confirmed = QMessageBox.warning(
            self,
            "저장된 Secret 삭제",
            (
                f"{_short_secret_id(selected.identifier)} 저장 항목을 삭제할까요? "
                "AWS Secret은 삭제되지 않습니다."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self._runner.submit(
            lambda: self._secrets.delete_saved(selected.require_id(), self._profile_id),
            lambda _value: self._saved_changed("저장된 Secret을 삭제했습니다."),
            self.error_raised.emit,
        )

    def _saved_changed(
        self, notice: str, saved_id: int | None = None, *, hydrate: bool = False
    ) -> None:
        self.notice_raised.emit(notice)
        if saved_id is None:
            self._clear_result()
            self.summary.setText("조회한 Secret이 없습니다.")
        self._pending_saved_id = saved_id
        self._hydrate_pending_selection = hydrate
        self.load_saved()

    def _terminal_failed(self, error: ApplicationError) -> None:
        self.terminal_fallback_button.setEnabled(True)
        self.error_raised.emit(error)

    def load_relays(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id

        def action() -> list[SecretRelayTarget]:
            return self._secrets.list_relay_targets(profile_id)

        def loaded(value: Any) -> None:
            if self._profile_id == profile_id:
                self._relays_loaded(value)

        def failed(error: ApplicationError) -> None:
            if self._profile_id == profile_id:
                self._relay_failed(error)

        if self._authenticated is None:
            self._runner.submit(action, loaded, failed)
        else:
            self._authenticated.submit(profile_id, action, loaded, failed, self._cancelled)

    def _relays_loaded(self, value: Any) -> None:
        self._relay_targets = list(value)
        self._relays_loaded_once = True
        self._render_relays()
        self._update_actions()

    def _render_relays(self) -> None:
        selected_id = self._pending_relay_instance_id
        self.relay_instance.clear()
        selected_index = -1
        for index, target in enumerate(self._relay_targets):
            self.relay_instance.addItem(
                f"{target.name or '이름 없음'} · {target.instance_id} · {target.platform_name}",
                target,
            )
            if target.instance_id == selected_id:
                selected_index = index
        if selected_id is not None and selected_index < 0:
            self.relay_instance.addItem(f"사용 불가 · {selected_id}", None)
            selected_index = self.relay_instance.count() - 1
        if selected_index >= 0:
            self.relay_instance.setCurrentIndex(selected_index)

    def _relay_failed(self, error: ApplicationError) -> None:
        self._relay_blocked = True
        self.relay_status.setText("연결 확인 실패")
        self._update_actions()
        self.error_raised.emit(error)

    def _mode_changed(self) -> None:
        via_ec2 = self.lookup_mode.currentData() == SecretLookupMode.VIA_EC2
        self.relay_panel.setVisible(via_ec2)
        self.relay_consent.setVisible(via_ec2)
        self.relay_notice.setVisible(via_ec2)
        self.hide_values()
        self._relay_revision += 1
        self._relay_test_busy = False
        if self._lookup_busy:
            self._clear_result()
        if via_ec2:
            self.secret_selector.hide()
            self.secret_id.show()
            self.catalog_status.setText("")
        elif self._catalog_entries and self._restored_identifier is None:
            self.secret_selector.show()
            self.secret_id.hide()
        self.relay_consent.setChecked(False)
        self.terminal_fallback_button.hide()
        self._update_actions()

    def _update_actions(self) -> None:
        self.catalog_status.setVisible(bool(self.catalog_status.text()))
        enabled = (
            self._profile_id is not None
            and bool(self._current_identifier())
            and not self._lookup_busy
        )
        self.relay_status.setProperty("unavailable", self._relay_blocked)
        self.relay_status.style().unpolish(self.relay_status)
        self.relay_status.style().polish(self.relay_status)
        self.test_relay_button.setEnabled(
            self._profile_id is not None
            and isinstance(self.relay_instance.currentData(), SecretRelayTarget)
            and not self._relay_test_busy
        )
        if self.lookup_mode.currentData() == SecretLookupMode.VIA_EC2:
            enabled = (
                enabled
                and isinstance(self.relay_instance.currentData(), SecretRelayTarget)
                and self.relay_consent.isChecked()
                and not self._relay_blocked
                and not self._relay_test_busy
            )
        self.get_button.setEnabled(enabled)
        self._selection_changed()


def _display(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _prompt_saved_secret(parent: QWidget, saved: SavedSecret | None) -> tuple[str, str] | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle("Secret 수정" if saved is not None else "Secret 등록")
    dialog.resize(520, 360)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    sequence = QLineEdit(str(saved.require_id()) if saved is not None else "저장 시 자동 생성")
    sequence.setObjectName("saved_secret_sequence")
    sequence.setReadOnly(True)
    original_id = saved.identifier if saved is not None else ""
    identifier = QLineEdit(_short_secret_id(original_id))
    identifier.setObjectName("saved_secret_identifier")
    secret_value = QPlainTextEdit(saved.value if saved is not None else "")
    secret_value.setObjectName("saved_secret_value")
    form.addRow("ID", sequence)
    form.addRow("Secret ID", identifier)
    form.addRow("Value", secret_value)
    layout.addLayout(form)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
    buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(identifier.text().strip()))
    identifier.textChanged.connect(
        lambda text: buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(
            bool(text.strip())
        )
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    edited_id = identifier.text().strip()
    return (
        original_id if edited_id == _short_secret_id(original_id) else edited_id,
        secret_value.toPlainText(),
    )


def _short_secret_id(identifier: str) -> str:
    """Shorten ARN presentation while preserving the original identifier for requests."""
    if identifier.startswith("arn:") and ":secret:" in identifier:
        return identifier.partition(":secret:")[2]
    return identifier
