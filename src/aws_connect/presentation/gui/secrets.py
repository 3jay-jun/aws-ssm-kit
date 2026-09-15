"""Secrets Manager GUI adapter backed by the shared Application Service."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.ec2_service import Ec2Service
from aws_connect.application.ports import ListedSecret
from aws_connect.application.secrets_service import (
    SecretField,
    SecretLookupMode,
    SecretRelayTarget,
    SecretResult,
    SecretsService,
    build_persistent_remote_secret_command,
)
from aws_connect.domain.errors import ApplicationError, AwsPermissionError
from aws_connect.domain.saved_secret import SavedSecret
from aws_connect.presentation.gui.authenticated import AuthenticatedGuiRunner, MfaCodeProvider
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
        self._profile_id: int | None = None
        self._result: SecretResult | None = None
        self._result_generation = 0
        self._revealed_paths: set[str] = set()
        self._catalog_entries: list[ListedSecret] = []
        self._saved_entries: list[SavedSecret] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 30, 34, 40)
        root.setSpacing(16)
        page_head = QHBoxLayout()
        heading_copy = QVBoxLayout()
        heading = QLabel("Secrets Manager")
        heading.setObjectName("page_title")
        subtitle = QLabel("현재 프로필에 조회 권한이 있는 시크릿만 표시합니다.")
        subtitle.setObjectName("page_subtitle")
        heading_copy.addWidget(heading)
        heading_copy.addWidget(subtitle)
        page_head.addLayout(heading_copy)
        page_head.addStretch()
        self.list_button = QPushButton("↻ 새로고침")
        self.list_button.setObjectName("secret_list")
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
        id_label = QLabel("Secret 검색")
        id_label.setObjectName("field_label")
        id_group.addWidget(id_label)
        self.secret_id = QLineEdit()
        self.secret_id.setObjectName("secret_id")
        self.secret_id.setPlaceholderText("Secret ID 또는 이름")
        self.secret_id.textChanged.connect(self._update_actions)
        id_group.addWidget(self.secret_id)
        self.secret_selector = QComboBox()
        self.secret_selector.setObjectName("secret_selector")
        self.secret_selector.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.secret_selector.setMinimumContentsLength(24)
        self.secret_selector.currentIndexChanged.connect(self._selector_changed)
        self.secret_selector.hide()
        id_group.addWidget(self.secret_selector)
        self.catalog_status = QLabel("Secret 목록 권한을 확인하는 중입니다.")
        self.catalog_status.setObjectName("helper_text")
        self.catalog_status.setWordWrap(True)
        id_group.addWidget(self.catalog_status)
        query.addLayout(id_group, 1)
        self.get_button = QPushButton("조회")
        self.get_button.setObjectName("secret_get")
        self.get_button.setProperty("variant", "primary")
        self.get_button.setEnabled(False)
        self.get_button.clicked.connect(self.get_secret)
        self.secret_id.returnPressed.connect(self.get_secret)
        query.addWidget(self.get_button, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addWidget(toolbar)

        relay_panel = QFrame()
        relay_panel.setObjectName("secret_relay_panel")
        relay_controls = QHBoxLayout(relay_panel)
        self.relay_instance = QComboBox()
        self.relay_instance.setObjectName("secret_relay_instance")
        self.relay_instance.currentIndexChanged.connect(self._update_actions)
        relay_controls.addWidget(QLabel("중계 EC2"))
        relay_controls.addWidget(self.relay_instance, 1)
        root.addWidget(relay_panel)
        self.relay_panel = relay_panel
        self.relay_warning = QLabel(
            "경유 조회는 SSM Run Command 출력에 Secret 원문을 일시적으로 남길 수 있습니다. "
            "앱 사용자에게 SendCommand/GetCommandInvocation, EC2 역할에 GetSecretValue 권한이 "
            "필요합니다. SendCommand 권한이 없다면 EC2 접속 화면에서 같은 인스턴스의 터미널을 "
            "열고 aws secretsmanager get-secret-value 명령을 직접 실행하세요."
        )
        self.relay_warning.setWordWrap(True)
        self.relay_warning.setObjectName("secret_relay_warning")
        root.addWidget(self.relay_warning)
        self.relay_consent = QCheckBox("위 위험을 이해했으며 이번 조회에 동의합니다.")
        self.relay_consent.setObjectName("secret_relay_consent")
        self.relay_consent.toggled.connect(self._update_actions)
        root.addWidget(self.relay_consent)
        self.terminal_fallback_button = QPushButton("EC2 직접 조회")
        self.terminal_fallback_button.setObjectName("secret_terminal_fallback")
        self.terminal_fallback_button.clicked.connect(self.open_terminal_fallback)
        self.terminal_fallback_button.hide()
        root.addWidget(self.terminal_fallback_button, alignment=Qt.AlignmentFlag.AlignLeft)
        content = QHBoxLayout()
        content.setSpacing(16)
        catalog_card = QFrame()
        catalog_card.setObjectName("secret_catalog_card")
        catalog_card.setProperty("role", "card")
        catalog_card.setFixedWidth(360)
        catalog = QVBoxLayout(catalog_card)
        catalog.setContentsMargins(20, 20, 20, 20)
        catalog_title = QLabel("저장된 Secret")
        catalog_title.setObjectName("section_title")
        catalog.addWidget(catalog_title)
        self.saved_status = QLabel("프로필을 선택하면 저장 항목을 불러옵니다.")
        self.saved_status.setObjectName("helper_text")
        catalog.addWidget(self.saved_status)
        self.catalog = QListWidget()
        self.catalog.setObjectName("secret_catalog")
        self.catalog.itemClicked.connect(self._catalog_selected)
        self.catalog.currentRowChanged.connect(lambda _row: self._selection_changed())
        catalog.addWidget(self.catalog, 1)
        saved_actions = QHBoxLayout()
        self.delete_saved_button = QPushButton("삭제")
        self.delete_saved_button.setProperty("role", "danger")
        self.edit_saved_button = QPushButton("수정")
        self.register_saved_button = QPushButton("등록")
        self.delete_saved_button.clicked.connect(self.delete_saved)
        self.edit_saved_button.clicked.connect(self.edit_saved)
        self.register_saved_button.clicked.connect(self.register_saved)
        saved_actions.addWidget(self.delete_saved_button)
        saved_actions.addStretch()
        saved_actions.addWidget(self.edit_saved_button)
        saved_actions.addWidget(self.register_saved_button)
        catalog.addLayout(saved_actions)
        content.addWidget(catalog_card)
        result_card = QFrame()
        result_card.setObjectName("secret_result_card")
        result_card.setProperty("role", "card")
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(20, 20, 20, 20)
        result_head = QHBoxLayout()
        self.summary = QLabel("Secret 값 · 조회한 Secret이 없습니다.")
        self.summary.setObjectName("secret_summary")
        result_head.addWidget(self.summary)
        result_head.addStretch()
        result_layout.addLayout(result_head)
        self.fields = QTableWidget(0, 2)
        self.fields.setObjectName("secret_fields")
        self.fields.setHorizontalHeaderLabels(["키", "값"])
        self.fields.horizontalHeader().hide()
        self.fields.verticalHeader().hide()
        self.fields.setShowGrid(False)
        self.fields.horizontalHeader().setStretchLastSection(True)
        self.fields.itemSelectionChanged.connect(self._selection_changed)
        self.fields.cellDoubleClicked.connect(lambda _row, _column: self.reveal_selected())
        use_first_column_selection_bar(self.fields)
        result_layout.addWidget(self.fields, 1)
        notice = QLabel(
            "SQLite에는 Secret 이름/ARN만 저장하며 조회 값은 저장하지 않습니다. "
            "RDS 터널 세션에는 선택한 host와 port만 복사할 수 있습니다."
        )
        notice.setObjectName("secret_notice")
        notice.setWordWrap(True)
        self.result_notice = notice
        result_layout.addWidget(notice)
        self.rds_button = QPushButton("host/port를 RDS 편집기로 복사")
        self.rds_button.setObjectName("secret_to_rds")
        self.rds_button.clicked.connect(self.copy_to_rds)
        result_layout.addWidget(self.rds_button, alignment=Qt.AlignmentFlag.AlignRight)
        content.addWidget(result_card, 1)
        root.addLayout(content, 1)
        self._selection_changed()
        self.lookup_mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

    def set_profile(self, profile_id: int | None) -> None:
        if self._profile_id == profile_id:
            return
        self._profile_id = profile_id
        self._clear_result()
        self.summary.setText("Secret 값 · 조회한 Secret이 없습니다.")
        self.secret_id.clear()
        self.relay_instance.clear()
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
            self.saved_status.setText("SQLite 저장 항목을 불러오는 중입니다…")
            self.load_saved()
            self.load_list()
            self.load_relays()

    def load_saved(self) -> None:
        if self._profile_id is None:
            return
        self._runner.submit(
            lambda: self._secrets.list_saved(self._profile_id),
            self._saved_loaded,
            self.error_raised.emit,
        )

    def _saved_loaded(self, value: Any) -> None:
        self._saved_entries = list(value)
        self.catalog.clear()
        for saved in self._saved_entries:
            item = QListWidgetItem(saved.identifier)
            item.setData(Qt.ItemDataRole.UserRole, saved)
            self.catalog.addItem(item)
        self.saved_status.setText(f"SQLite 저장 항목 {len(self._saved_entries)}개")
        self._selection_changed()

    def load_list(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        self.list_button.setEnabled(False)

        def action() -> list[ListedSecret]:
            return self._secrets.list(profile_id)

        if self._authenticated is None:
            self._runner.submit(action, self._list_loaded, self._list_failed)
        else:
            self._authenticated.submit(
                profile_id, action, self._list_loaded, self._list_failed, self._cancelled
            )

    def _list_loaded(self, value: Any) -> None:
        self.list_button.setEnabled(self._profile_id is not None)
        self._catalog_entries = list(value)
        self.catalog_status.setText(
            f"조회 가능한 Secret {len(self._catalog_entries)}개"
            if self._catalog_entries
            else "조회 권한은 있지만 표시할 Secret이 없습니다."
        )
        self.secret_selector.clear()
        for secret in self._catalog_entries:
            self.secret_selector.addItem(secret.name, secret)
        self.secret_id.hide()
        self.secret_selector.show()
        self._selector_changed()

    def _selector_changed(self) -> None:
        secret = self.secret_selector.currentData()
        if isinstance(secret, ListedSecret):
            self.secret_id.setText(secret.arn)
        self._update_actions()

    def _catalog_selected(self, item: QListWidgetItem) -> None:
        secret = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(secret, SavedSecret):
            self.secret_id.setText(secret.identifier)
            self._request_secret(secret.identifier)

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
        if not self.secret_selector.isHidden() and isinstance(secret, ListedSecret):
            return secret.arn
        return self.secret_id.text().strip()

    def _request_secret(self, identifier: str) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        self.secret_id.setText(identifier)
        mode = self.lookup_mode.currentData()
        relay = self.relay_instance.currentData()
        if mode == SecretLookupMode.VIA_EC2 and (
            not isinstance(relay, SecretRelayTarget) or not self.relay_consent.isChecked()
        ):
            return
        self._clear_result()
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

        if self._authenticated is None:
            self._runner.submit(action, self._loaded, self._failed)
        else:
            self._authenticated.submit(
                profile_id, action, self._loaded, self._failed, self._cancelled
            )

    def _cancelled(self) -> None:
        self._update_actions()
        self.list_button.setEnabled(self._profile_id is not None)
        self.summary.setText("MFA 인증을 취소했습니다.")

    def _loaded(self, value: Any) -> None:
        self._result = value
        self._result_generation += 1
        self._revealed_paths.clear()
        self._update_actions()
        self.summary.setText(f"Secret 값 · {self._result.secret_id} · {self._result.kind.value}")
        self._render()
        self.load_saved()

    def _render(self) -> None:
        result = self._result
        self.fields.setRowCount(0 if result is None else len(result.fields))
        if result is None:
            self._selection_changed()
            return
        for row, field in enumerate(result.fields):
            self.fields.setItem(row, 0, QTableWidgetItem(field.path))
            value = field.reveal() if field.path in self._revealed_paths else field.masked_value
            self.fields.setItem(row, 1, QTableWidgetItem(_display(value)))
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
        self._result_generation += 1
        self._result = None
        self._revealed_paths.clear()
        self.fields.setRowCount(0)
        self.terminal_fallback_button.hide()

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
        has_result = self._result is not None
        saved_selected = 0 <= self.catalog.currentRow() < len(self._saved_entries)
        self.delete_saved_button.setEnabled(saved_selected)
        self.edit_saved_button.setEnabled(saved_selected)
        self.register_saved_button.setEnabled(
            self._profile_id is not None and bool(self._current_identifier())
        )
        self.rds_button.setEnabled(has_result)
        self.rds_button.setVisible(has_result)
        self.result_notice.setVisible(has_result)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        """Keep the approved desktop codebox while avoiding overlap at 720p."""

        self.fields.setMinimumHeight(66 if self.height() < 650 else 226)
        super().resizeEvent(event)

    def _failed(self, error: ApplicationError) -> None:
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
        identifier = self._current_identifier()
        if not identifier:
            identifier, accepted = QInputDialog.getText(self, "Secret 등록", "Secret 이름 또는 ARN")
            if not accepted:
                return
        self._runner.submit(
            lambda: self._secrets.remember(identifier, self._profile_id),
            lambda _value: self._saved_changed("Secret을 등록했습니다."),
            self.error_raised.emit,
        )

    def edit_saved(self) -> None:
        row = self.catalog.currentRow()
        if self._profile_id is None or not (0 <= row < len(self._saved_entries)):
            return
        selected = self._saved_entries[row]
        identifier, accepted = QInputDialog.getText(
            self,
            "저장된 Secret 수정",
            "Secret 이름 또는 ARN",
            text=selected.identifier,
        )
        if not accepted:
            return
        self._runner.submit(
            lambda: self._secrets.update_saved(selected.require_id(), identifier, self._profile_id),
            lambda _value: self._saved_changed("저장된 Secret을 수정했습니다."),
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
            f"{selected.identifier} 저장 항목을 삭제할까요? AWS Secret은 삭제되지 않습니다.",
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

    def _saved_changed(self, notice: str) -> None:
        self.notice_raised.emit(notice)
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

        if self._authenticated is None:
            self._runner.submit(action, self._relays_loaded, self._relay_failed)
        else:
            self._authenticated.submit(
                profile_id, action, self._relays_loaded, self._relay_failed, self._cancelled
            )

    def _relays_loaded(self, value: Any) -> None:
        self.relay_instance.clear()
        for target in value:
            self.relay_instance.addItem(
                f"{target.name or '이름 없음'} · {target.instance_id} · {target.platform_name}",
                target,
            )
        self._update_actions()

    def _relay_failed(self, error: ApplicationError) -> None:
        self._update_actions()
        self.error_raised.emit(error)

    def _mode_changed(self) -> None:
        via_ec2 = self.lookup_mode.currentData() == SecretLookupMode.VIA_EC2
        self.relay_panel.setVisible(via_ec2)
        self.relay_warning.setVisible(via_ec2)
        self.relay_consent.setVisible(via_ec2)
        self.relay_consent.setChecked(False)
        self.terminal_fallback_button.hide()
        self._update_actions()

    def _update_actions(self) -> None:
        enabled = self._profile_id is not None and bool(self._current_identifier())
        if self.lookup_mode.currentData() == SecretLookupMode.VIA_EC2:
            enabled = (
                enabled
                and isinstance(self.relay_instance.currentData(), SecretRelayTarget)
                and self.relay_consent.isChecked()
            )
        self.get_button.setEnabled(enabled)
        self._selection_changed()


def _display(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
