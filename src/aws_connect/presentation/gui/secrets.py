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
from aws_connect.presentation.gui.authenticated import AuthenticatedGuiRunner, MfaCodeProvider
from aws_connect.presentation.gui.clipboard import copy_temporarily
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
        self.secret_id.textChanged.connect(self._render_catalog)
        id_group.addWidget(self.secret_id)
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
        self.relay_refresh = QPushButton("Online EC2 불러오기")
        self.relay_refresh.setObjectName("secret_relay_refresh")
        self.relay_refresh.clicked.connect(self.load_relays)
        relay_controls.addWidget(QLabel("중계 EC2"))
        relay_controls.addWidget(self.relay_instance, 1)
        relay_controls.addWidget(self.relay_refresh)
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
        self.catalog = QListWidget()
        self.catalog.setObjectName("secret_catalog")
        self.catalog.itemActivated.connect(self._catalog_selected)
        catalog.addWidget(self.catalog, 1)
        content.addWidget(catalog_card)
        result_card = QFrame()
        result_card.setObjectName("secret_result_card")
        result_card.setProperty("role", "card")
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(20, 20, 20, 20)
        result_head = QHBoxLayout()
        self.summary = QLabel("조회한 Secret이 없습니다.")
        self.summary.setObjectName("secret_summary")
        result_head.addWidget(self.summary)
        result_head.addStretch()
        self.copy_button = QPushButton("복사")
        self.copy_button.setObjectName("secret_copy")
        self.copy_button.setProperty("size", "small")
        self.save_field_button = QPushButton("키-값 저장")
        self.save_field_button.setObjectName("secret_save_field")
        self.save_field_button.setProperty("size", "small")
        self.copy_button.clicked.connect(self.copy_selected)
        self.save_field_button.clicked.connect(self.save_selected_field)
        result_head.addWidget(self.save_field_button)
        result_head.addWidget(self.copy_button)
        result_layout.addLayout(result_head)
        self.fields = QTableWidget(0, 2)
        self.fields.setObjectName("secret_fields")
        self.fields.setHorizontalHeaderLabels(["키", "값"])
        self.fields.horizontalHeader().hide()
        self.fields.verticalHeader().hide()
        self.fields.setShowGrid(False)
        self.fields.horizontalHeader().setStretchLastSection(True)
        self.fields.itemSelectionChanged.connect(self._selection_changed)
        use_first_column_selection_bar(self.fields)
        result_layout.addWidget(self.fields, 1)
        notice = QLabel(
            "Secret 조회 결과는 SQLite와 로그에 저장하지 않습니다. "
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
        self.summary.setText("조회한 Secret이 없습니다.")
        self.relay_instance.clear()
        self.relay_consent.setChecked(False)
        self.list_button.setEnabled(profile_id is not None)
        self._update_actions()
        self.catalog.clear()
        self._selection_changed()
        if profile_id is not None:
            self.load_list()
            self.load_relays()

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
        self._render_catalog()

    def _render_catalog(self) -> None:
        query = self.secret_id.text().strip().casefold()
        self.catalog.clear()
        for secret in self._catalog_entries:
            if query and query not in f"{secret.name} {secret.arn}".casefold():
                continue
            item = QListWidgetItem(f"{secret.name}\n수정 시간 정보 없음")
            item.setData(Qt.ItemDataRole.UserRole, secret)
            self.catalog.addItem(item)

    def _catalog_selected(self, item: QListWidgetItem) -> None:
        secret = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(secret, ListedSecret):
            self.secret_id.setText(secret.arn)
            self.get_secret()

    def _list_failed(self, error: ApplicationError) -> None:
        # ListSecrets is optional. A failure must not disable direct GetSecretValue.
        self.list_button.setEnabled(self._profile_id is not None)
        self._update_actions()
        if isinstance(error, AwsPermissionError):
            self.notice_raised.emit("Secret 목록 권한이 없어 이름 또는 ARN 직접 검색을 유지합니다.")
            return
        self.error_raised.emit(error)

    def get_secret(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        identifier = self.secret_id.text()
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
        self.summary.setText(f"{self._result.secret_id} · {self._result.kind.value}")
        self._render()

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

    def copy_selected(self) -> None:
        field = self._selected_field()
        if field is None:
            return
        value = _display(field.reveal())
        copy_temporarily(value)
        self.notice_raised.emit("선택한 값을 복사했습니다. 30초 후 클립보드에서 제거합니다.")

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
        selected = self._selected_field() is not None
        has_result = self._result is not None
        self.copy_button.setEnabled(selected)
        field = self._selected_field()
        self.save_field_button.setEnabled(
            field is not None
            and self._result is not None
            and self._result.kind.value == "json"
            and "." not in field.path
            and "[" not in field.path
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
                self.secret_id.text().strip(), relay.platform_name
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

    def save_selected_field(self) -> None:
        field = self._selected_field()
        result = self._result
        if field is None or result is None or self._profile_id is None:
            return
        initial = "" if field.sensitive else _display(field.reveal())
        value, accepted = QInputDialog.getText(
            self,
            "Secret 키-값 저장",
            f"{field.path}의 새 값",
            text=initial,
        )
        if not accepted:
            return
        confirmed = QMessageBox.warning(
            self,
            "AWS Secret 변경 확인",
            "기존 Secret에 새 버전을 저장합니다. 계속할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        profile_id = self._profile_id
        self.save_field_button.setEnabled(False)

        def action() -> SecretResult:
            return self._secrets.save_top_level_field(result, field.path, value, profile_id)

        if self._authenticated is None:
            self._runner.submit(action, self._field_saved, self._failed)
        else:
            self._authenticated.submit(
                profile_id, action, self._field_saved, self._failed, self._cancelled
            )

    def _field_saved(self, value: Any) -> None:
        self._loaded(value)
        self.notice_raised.emit("AWS Secret에 새 버전을 저장했습니다.")

    def _terminal_failed(self, error: ApplicationError) -> None:
        self.terminal_fallback_button.setEnabled(True)
        self.error_raised.emit(error)

    def load_relays(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        self.relay_refresh.setEnabled(False)

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
        self.relay_refresh.setEnabled(self._profile_id is not None)
        self._update_actions()

    def _relay_failed(self, error: ApplicationError) -> None:
        self.relay_refresh.setEnabled(self._profile_id is not None)
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
        enabled = self._profile_id is not None
        if self.lookup_mode.currentData() == SecretLookupMode.VIA_EC2:
            enabled = (
                enabled
                and isinstance(self.relay_instance.currentData(), SecretRelayTarget)
                and self.relay_consent.isChecked()
            )
        self.get_button.setEnabled(enabled)
        self.relay_refresh.setEnabled(self._profile_id is not None)


def _display(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
