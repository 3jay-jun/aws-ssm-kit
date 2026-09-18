"""EC2 and RDS GUI adapters backed exclusively by Application Services."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.ec2_service import (
    Ec2Service,
    Ec2Target,
    Ec2TargetFilter,
    filter_ec2_targets,
)
from aws_connect.application.operations import OperationContext, OperationResult, OperationState
from aws_connect.application.ports import RdsEndpoint
from aws_connect.application.rds_endpoint_service import RdsEndpointService
from aws_connect.application.rds_tunnel_operation import RdsTunnelOperationCoordinator
from aws_connect.application.rds_tunnel_service import (
    SaveTunnelSessionRequest,
    StartTunnelRequest,
    TunnelConnectionResult,
    TunnelSessionService,
)
from aws_connect.domain.errors import ApplicationError, AwsPermissionError
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession
from aws_connect.presentation.gui.authenticated import AuthenticatedGuiRunner
from aws_connect.presentation.gui.clipboard import copy_temporarily
from aws_connect.presentation.gui.icons import gui_icon, icon_text, set_button_icon
from aws_connect.presentation.gui.list_rows import (
    set_compact_list_row,
    update_list_row_separators,
)
from aws_connect.presentation.gui.table_selection import use_first_column_selection_bar
from aws_connect.presentation.gui.tasks import GuiTaskRunner
from aws_connect.presentation.gui.view_models import (
    ActiveTunnelSummaryViewModel,
    ec2_connection_time,
)

MfaCodeProvider = Callable[[QWidget, str], str | None]
DeleteConfirmation = Callable[[QWidget, int], bool]
CloneNameProvider = Callable[[QWidget, str], tuple[str, bool]]
DiscardConfirmation = Callable[[QWidget], bool]


class ActiveTunnelRow(QWidget):
    """Reusable tunnel row with policy-compliant copy and lifecycle actions."""

    copied = Signal(str)
    stop_requested = Signal(str)

    def __init__(self, summary: ActiveTunnelSummaryViewModel) -> None:
        super().__init__()
        self.summary = summary
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        label = QLabel(summary.display_text)
        label.setObjectName("active_tunnel_address")
        layout.addWidget(label, 1)
        copy_button = QPushButton("주소 복사")
        copy_button.setAccessibleName(f"{summary.name} 로컬 주소 복사")
        set_button_icon(copy_button, "common-clipboard.svg")
        copy_button.clicked.connect(self._copy)
        layout.addWidget(copy_button)
        stop_button = QPushButton("연결 종료")
        stop_button.setProperty("size", "small")
        stop_button.setProperty("variant", "danger")
        stop_button.setAccessibleName(f"{summary.name} 터널 종료")
        set_button_icon(stop_button, "common-stop.svg")
        stop_button.setEnabled(summary.operation_id is not None)
        stop_button.clicked.connect(self._stop)
        layout.addWidget(stop_button)

    def _copy(self) -> None:
        copy_temporarily(self.summary.local_address)
        self.copied.emit(self.summary.local_address)

    def _stop(self) -> None:
        if self.summary.operation_id is not None:
            self.stop_requested.emit(self.summary.operation_id)


class Ec2Page(QWidget):
    """Search, select and launch EC2 sessions using the shared EC2 service."""

    error_raised = Signal(object)
    notice_raised = Signal(str)

    def __init__(
        self,
        service: Ec2Service,
        runner: GuiTaskRunner,
        authenticated: AuthenticatedOperationCoordinator | None = None,
        mfa_code_provider: MfaCodeProvider | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("ec2_page")
        self._service = service
        self._runner = runner
        self._authenticated = (
            AuthenticatedGuiRunner(authenticated, runner, self, mfa_code_provider)
            if authenticated is not None and mfa_code_provider is not None
            else None
        )
        self._profile_id: int | None = None
        self._region = "ap-northeast-2"
        self._targets: list[Ec2Target] = []
        self._target_by_id: dict[str, Ec2Target] = {}
        self._action_busy = False
        self._load_revision = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 40)
        layout.setSpacing(16)
        heading = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        title = QLabel("EC2 접속")
        title.setObjectName("page_title")
        subtitle = QLabel("인스턴스를 선택하면 별도 Windows Terminal에서 셸을 실행합니다.")
        subtitle.setObjectName("page_subtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        heading.addLayout(title_block)
        heading.addStretch()
        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("ec2_refresh")
        self.refresh_button.setProperty("action_button", True)
        set_button_icon(self.refresh_button, "common-refresh.svg", size=22, tooltip="목록 새로고침")
        self.refresh_button.clicked.connect(self.reload)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        layout.addSpacing(8)
        filters = QHBoxLayout()
        filters.setSpacing(14)
        self.region = QComboBox()
        self.region.setEditable(True)
        self.region.addItem(self._region)
        self.region.setMinimumWidth(160)
        self.region.setMaximumWidth(194)
        self.region.setObjectName("ec2_region")
        self.region.setAccessibleName("EC2 Region")
        self.status = QComboBox()
        self.status.setObjectName("ec2_status_filter")
        self.status.setAccessibleName("인스턴스 상태")
        self.status.setMinimumWidth(130)
        self.status.setMaximumWidth(178)
        self.status.addItem("전체", "all")
        self.status.addItem("실행 중", "running")
        self.status.addItem("중지됨", "stopped")
        self.filter = QLineEdit()
        self.filter.setObjectName("ec2_filter")
        self.filter.setPlaceholderText("이름, Instance ID, Private IP로 검색")
        self.filter.addAction(
            gui_icon("common-search.svg"), QLineEdit.ActionPosition.LeadingPosition
        )
        self.favorites_only = QCheckBox("즐겨찾기만")
        self.favorites_only.setObjectName("ec2_favorites_only")
        self.favorites_only.toggled.connect(self._render_targets)
        self.filter.textChanged.connect(self._render_targets)
        self.status.currentIndexChanged.connect(self._render_targets)
        self.region.activated.connect(self.reload)
        region_editor = self.region.lineEdit()
        if region_editor is not None:
            region_editor.editingFinished.connect(self._reload_changed_region)
        filters.addWidget(_field("Region", self.region))
        filters.addWidget(_field("상태", self.status))
        filters.addWidget(_field("검색", self.filter), 1)
        filters.addWidget(self.favorites_only, alignment=Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(filters)
        self.table_card = QFrame()
        self.table_card.setObjectName("content_card")
        card_layout = QVBoxLayout(self.table_card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        self.table = QTableWidget(0, 8)
        self.table.setObjectName("ec2_targets")
        self.table.setHorizontalHeaderLabels(
            ["★", "이름", "Instance ID", "Private IP", "EC2 상태", "SSM", "최근 접속", "작업"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.verticalHeader().setMinimumSectionSize(54)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setResizeContentsPrecision(-1)
        self.table.horizontalHeader().setMinimumSectionSize(48)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 112)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 128)
        self.table.setShowGrid(False)
        use_first_column_selection_bar(self.table)
        card_layout.addWidget(self.table)
        layout.addWidget(self.table_card, 1)
        self.summary_bar = QFrame()
        self.summary_bar.setObjectName("ec2_selection_summary")
        summary_layout = QHBoxLayout(self.summary_bar)
        summary_layout.setContentsMargins(16, 10, 16, 10)
        summary_layout.setSpacing(12)
        self.summary_icon = QLabel()
        self.summary_icon.setPixmap(
            gui_icon("common-circle-check.svg", color="#00a166", size=22).pixmap(22, 22)
        )
        summary_layout.addWidget(self.summary_icon)
        self.session_state = QLabel("인스턴스를 선택하세요.")
        self.session_state.setTextFormat(Qt.TextFormat.PlainText)
        self.session_state.setWordWrap(True)
        self.session_state.setMinimumWidth(0)
        self.session_state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.session_state.setObjectName("ec2_session_state")
        summary_layout.addWidget(self.session_state, 1)
        layout.addWidget(self.summary_bar)
        self.summary_icon.hide()
        # Kept as a non-rendered compatibility action; visible actions live in each table row.
        self.open_button = QPushButton("터미널 열기")
        self.open_button.setObjectName("ec2_open")
        self.open_button.hide()
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self.refresh_session_states)

    def set_profile(self, profile_id: int | None, region: str | None = None) -> None:
        selected_region = region or self._region
        if self._profile_id == profile_id and self._region == selected_region:
            return
        self._load_revision += 1
        self._profile_id = profile_id
        self._region = selected_region
        self.region.setCurrentText(selected_region)
        self._targets = []
        self._render_targets()
        if profile_id is not None:
            self.reload()

    def _reload_changed_region(self) -> None:
        if self.region.currentText().strip() != self._region:
            self.reload()

    def reload(self) -> None:
        if self._profile_id is None:
            return
        self._load_revision += 1
        revision = self._load_revision
        profile_id = self._profile_id
        region = self.region.currentText().strip()
        if self.region.findText(region) < 0:
            self.region.addItem(region)
        if region != self._region:
            self._region = region
            self._targets = []
            self._render_targets()
        self.refresh_button.setEnabled(False)

        def loaded(value: Any) -> None:
            if revision == self._load_revision:
                self._targets_loaded(value)

        def failed(error: ApplicationError) -> None:
            if revision == self._load_revision:
                self._failed(error)

        def cancelled() -> None:
            if revision == self._load_revision:
                self._cancelled()

        def action() -> list[Ec2Target]:
            return self._service.list_targets_in_region(profile_id, region)

        if self._authenticated is None:
            self._runner.submit(action, loaded, failed)
        else:
            self._authenticated.submit(profile_id, action, loaded, failed, cancelled)

    def _targets_loaded(self, value: Any) -> None:
        self._action_busy = False
        self.refresh_button.setEnabled(True)
        self._targets = list(value)
        self._render_targets()

    def _render_targets(self) -> None:
        selected = self._selected_target()
        blocker = QSignalBlocker(self.table)
        visible = filter_ec2_targets(
            self._targets,
            Ec2TargetFilter(
                keyword=self.filter.text(),
                ping_status="all",
                instance_state=str(self.status.currentData()),
                favorites_only=self.favorites_only.isChecked(),
            ),
        )
        self._target_by_id = {target.instance_id: target for target in visible}
        self.table.clearContents()
        self.table.setRowCount(len(visible))
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        for row, target in enumerate(visible):
            values = {
                0: "",
                1: target.name or "—",
                2: target.instance_id,
                3: target.private_ip_address or "—",
                4: "",
                5: "",
                6: ec2_connection_time(target.last_connected_at),
                7: "",
            }
            for column, value in values.items():
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, target.instance_id)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
            self.table.setCellWidget(
                row,
                4,
                _centered_cell(
                    _status_label(
                        _instance_state_label(target.instance_state),
                        "success" if target.instance_state == "running" else "danger",
                    )
                ),
            )
            self.table.setCellWidget(
                row,
                5,
                _centered_cell(
                    _status_label(
                        "준비됨" if target.ssm_ready else "지원 안됨",
                        "success" if target.ssm_ready else "danger",
                        icon="common-circle-check.svg" if target.ssm_ready else "common-ban.svg",
                    )
                ),
            )
            favorite = QPushButton()
            favorite.setObjectName("ec2_favorite")
            favorite.setProperty("icon_only", True)
            set_button_icon(
                favorite,
                "star-solid-full.svg" if target.favorite else "star-regular-full.svg",
                color="#2563eb" if target.favorite else "#172033",
            )
            favorite.setIconSize(QSize(18, 18))
            favorite.setFixedSize(30, 30)
            favorite_action = "해제" if target.favorite else "추가"
            favorite.setAccessibleName(
                f"{target.name or target.instance_id} 즐겨찾기 {favorite_action}"
            )
            favorite.setToolTip(f"즐겨찾기 {favorite_action}")
            favorite.clicked.connect(
                lambda _checked=False, value=target: self.toggle_favorite(value)
            )
            self.table.setCellWidget(row, 0, _centered_cell(favorite))
            action = QPushButton(_target_action_text(target))
            action.setObjectName("ec2_row_action")
            if target.instance_state == "stopped":
                action.setText("")
                set_button_icon(action, "common-start.svg", tooltip=_target_action_text(target))
            elif _target_action_role(target) == "primary":
                action.setText("")
                set_button_icon(action, "ec2-terminal.svg", tooltip="터미널 열기")
            else:
                action.setText("")
                set_button_icon(
                    action,
                    "ec2-terminal.svg",
                    tooltip=_target_action_text(target),
                    color="#94a3b8",
                )
            action.setProperty("action_button", True)
            action.setProperty("variant", _target_action_role(target))
            action.setEnabled(_target_action_enabled(target) and not self._action_busy)
            action.clicked.connect(
                lambda _checked=False, value=target: self.run_target_action(value)
            )
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(8, 4, 8, 4)
            action_layout.setSpacing(10)
            action_layout.addWidget(action)
            more = QPushButton()
            set_button_icon(more, "common-more.svg", tooltip="더보기")
            more.setObjectName("ec2_more")
            more.setProperty("action_button", True)
            more.setToolTip("더보기")
            more.setAccessibleName(f"{target.name or target.instance_id} 더보기")
            more.clicked.connect(
                lambda _checked=False, value=target, button=more: self._show_target_menu(
                    value, button
                )
            )
            action_layout.addWidget(more)
            action_layout.addStretch()
            action_item = self.table.item(row, 7)
            if action_item is not None:
                action_item.setSizeHint(QSize(132, 58))
            self.table.setCellWidget(row, 7, actions)
            self.table.setRowHeight(row, 58)
        if selected is not None:
            self._select_target(selected.instance_id)
        del blocker
        self._sync_actions()

    def _selected_target(self) -> Ec2Target | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return self._target_by_id.get(str(item.data(Qt.ItemDataRole.UserRole))) if item else None

    def _sync_actions(self) -> None:
        target = self._selected_target()
        self.open_button.setEnabled(
            target is not None
            and target.ssm_ready
            and target.instance_state in {None, "running"}
            and self._profile_id is not None
            and not self._action_busy
        )
        self.summary_icon.setVisible(target is not None and target.last_connected_at is not None)
        if target is None:
            self.session_state.setText("인스턴스를 선택하세요.")
            return
        history = (
            f"마지막 연결 성공 ({ec2_connection_time(target.last_connected_at)})"
            if target.last_connected_at is not None
            else "연결 이력 없음"
        )
        self.session_state.setText(
            f"선택한 인스턴스: {target.instance_id} · {target.name or '-'} · {history}"
        )

    def _show_target_menu(self, target: Ec2Target, button: QPushButton) -> None:
        self._select_target(target.instance_id)
        menu = QMenu(button)
        menu.setObjectName("ec2_target_menu")
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        copy_id = menu.addAction(gui_icon("common-copy.svg"), "인스턴스 ID 복사")
        copy_id.triggered.connect(lambda: copy_temporarily(target.instance_id))
        copy_ip = menu.addAction(gui_icon("common-copy.svg"), "Private IP 복사")
        copy_ip.setEnabled(bool(target.private_ip_address))
        copy_ip.triggered.connect(lambda: copy_temporarily(target.private_ip_address or ""))
        tags = menu.addAction(gui_icon("common-tag.svg"), "태그 보기")
        tags.triggered.connect(lambda: self._show_tags(target))
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _show_tags(self, target: Ec2Target) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("ec2_tags_dialog")
        dialog.setWindowTitle("태그 보기")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.resize(560, 360)
        layout = QVBoxLayout(dialog)
        heading = QLabel(target.instance_id)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(heading)
        table = QTableWidget(len(target.tags), 2)
        table.setHorizontalHeaderLabels(["키", "값"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, (key, value) in enumerate(target.tags):
            table.setItem(row, 0, QTableWidgetItem(key))
            table.setItem(row, 1, QTableWidgetItem(value))
        layout.addWidget(table)
        if not target.tags:
            layout.addWidget(QLabel("표시할 태그가 없습니다."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        dialog.show()

    def run_target_action(self, target: Ec2Target) -> None:
        if self._action_busy or self._profile_id is None or not _target_action_enabled(target):
            return
        self._select_target(target.instance_id)
        if target.instance_state == "stopped":
            self._run_power_action(target, "start")
        else:
            self.open_selected()

    def toggle_favorite(self, target: Ec2Target) -> None:
        if self._action_busy or self._profile_id is None:
            return
        profile_id = self._profile_id
        region = self.region.currentText().strip()
        self._action_busy = True
        self._render_targets()
        self._runner.submit(
            lambda: self._service.set_favorite(
                target.instance_id, not target.favorite, profile_id, region=region
            ),
            lambda _value: self.reload(),
            self._failed,
        )

    def _run_power_action(self, target: Ec2Target, action: str) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        region = self.region.currentText().strip()
        self._action_busy = True
        self._render_targets()
        self.notice_raised.emit("인스턴스 시작 요청 중…")
        operation = (
            self._service.start_instance if action == "start" else self._service.reboot_instance
        )

        def request() -> object:
            return operation(target.instance_id, profile_id, region=region)

        if self._authenticated is None:
            self._runner.submit(request, lambda _value: self._power_requested(action), self._failed)
        else:
            self._authenticated.submit(
                profile_id,
                request,
                lambda _value: self._power_requested(action),
                self._failed,
                self._cancelled,
            )

    def _power_requested(self, action: str) -> None:
        self._action_busy = False
        self.notice_raised.emit(
            "시작 요청이 접수되었습니다. 상태를 새로고침하세요."
            if action == "start"
            else "재부팅 요청이 접수되었습니다. 상태를 새로고침하세요."
        )
        self.reload()

    def _select_target(self, instance_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == instance_id:
                self.table.selectRow(row)
                return

    def open_selected(self) -> None:
        target = self._selected_target()
        if (
            self._action_busy
            or target is None
            or not target.ssm_ready
            or target.instance_state not in {None, "running"}
        ):
            return
        row = self.table.currentRow()
        if row < 0 or self._profile_id is None:
            return
        selected = self.table.item(row, 0)
        if selected is None:
            return
        instance_id = str(selected.data(Qt.ItemDataRole.UserRole))
        profile_id = self._profile_id
        region = self.region.currentText().strip()
        self.open_button.setEnabled(False)
        self._action_busy = True
        self._render_targets()
        self.notice_raised.emit("외부 터미널 시작 중…")

        def action(context: OperationContext | None = None) -> object:
            return self._service.connect_external(
                instance_id,
                profile_id,
                region=region,
                context=context,
            )

        if self._authenticated is None:
            self._runner.submit(lambda: action(), self._external_started, self._failed)
        else:
            self._authenticated.submit_long(
                profile_id,
                action,
                self._external_started,
                self._failed,
                on_cancel=self._cancelled,
            )

    def _external_started(self, value: Any) -> None:
        self.notice_raised.emit("EC2 세션을 외부 Windows Terminal에서 열었습니다.")
        self._action_busy = False
        self.reload()
        self._poll_timer.start()

    def refresh_session_states(self) -> None:
        self._runner.submit(
            self._service.reap_external_sessions,
            self._sessions_loaded,
            self._failed,
        )

    def _sessions_loaded(self, value: Any) -> None:
        all_handles = list(value)
        handles = [item for item in all_handles if item.profile_id == self._profile_id]
        any_running = any(item.state is OperationState.RUNNING for item in all_handles)
        for handle in handles:
            if handle.error is not None:
                self.error_raised.emit(handle.error)
        self._sync_actions()
        if not any_running:
            self._poll_timer.stop()

    def _failed(self, error: ApplicationError) -> None:
        self._action_busy = False
        self._render_targets()
        self.refresh_button.setEnabled(self._profile_id is not None)
        self._sync_actions()
        self.error_raised.emit(error)

    def _cancelled(self) -> None:
        self.refresh_button.setEnabled(self._profile_id is not None)
        self._action_busy = False
        self._render_targets()
        self.notice_raised.emit("MFA 인증 취소")


class RdsPage(QWidget):
    """Saved session editor and GUI-owned tunnel lifecycle adapter."""

    error_raised = Signal(object)
    notice_raised = Signal(str)
    tunnels_changed = Signal(object)

    def __init__(
        self,
        sessions: TunnelSessionService,
        tunnels: RdsTunnelOperationCoordinator,
        ec2: Ec2Service,
        runner: GuiTaskRunner,
        mfa_code_provider: MfaCodeProvider,
        delete_confirmation: DeleteConfirmation,
        clone_name_provider: CloneNameProvider | None = None,
        authenticated: AuthenticatedOperationCoordinator | None = None,
        discard_confirmation: DiscardConfirmation | None = None,
        endpoints: RdsEndpointService | None = None,
        rename_name_provider: CloneNameProvider | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("rds_page")
        self._sessions = sessions
        self._tunnels = tunnels
        self._ec2 = ec2
        self._runner = runner
        self._mfa_code_provider = mfa_code_provider
        self._delete_confirmation = delete_confirmation
        self._clone_name_provider = clone_name_provider or _ask_clone_name
        self._discard_confirmation = discard_confirmation or _confirm_discard
        self._authenticated = (
            AuthenticatedGuiRunner(authenticated, runner, self, mfa_code_provider)
            if authenticated is not None
            else None
        )
        self._rename_name_provider = rename_name_provider or _ask_rename_name
        self._endpoints = endpoints
        self._profile_id: int | None = None
        self._saved: dict[int, TunnelSession] = {}
        self._active: dict[str, TunnelConnectionResult] = {}
        self._selected_id: int | None = None
        self._draft_visible = False
        self._relays_ready = False
        self._connection_busy = False
        self._editor_baseline: tuple[object, ...] | None = None
        self._port_available: bool | None = None
        self._port_revision = 0
        self._port_timer = QTimer(self)
        self._port_timer.setSingleShot(True)
        self._port_timer.setInterval(250)
        self._port_timer.timeout.connect(self._check_local_port)
        self._build_ui()
        self.local_port.valueChanged.connect(self._schedule_port_check)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self.refresh_active)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 30, 34, 40)
        root.setSpacing(16)
        heading = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        title = QLabel("RDS 터널")
        title.setObjectName("page_title")
        subtitle = QLabel("터널 연결 정보를 저장하고 필요할 때 다시 사용할 수 있습니다.")
        subtitle.setObjectName("page_subtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        heading.addLayout(title_block)
        heading.addStretch()
        self.new_button = QPushButton("+ 새 터널")
        self.new_button.setObjectName("rds_new")
        self.new_button.setProperty("variant", "primary")
        self.new_button.clicked.connect(lambda: self.new_session())
        root.addLayout(heading)
        root.addSpacing(8)
        content = QHBoxLayout()
        content.setSpacing(16)
        self.session_card = QFrame()
        self.session_card.setObjectName("session_list_card")
        self.session_card.setFixedWidth(310)
        left = QVBoxLayout(self.session_card)
        left.setContentsMargins(8, 8, 8, 8)
        left.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("rds_search")
        self.search.setPlaceholderText("이름 또는 RDS Host 검색")
        self.search.setAccessibleName("저장 세션 검색")
        self.search.addAction(
            gui_icon("common-search.svg"), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search.textChanged.connect(self._render_sessions)
        self.session_list = QListWidget()
        self.session_list.setObjectName("rds_sessions")
        self.session_list.currentItemChanged.connect(self._select_session)
        left.addWidget(self.new_button)
        left.addWidget(self.search)
        left.addWidget(self.session_list, 1)
        content.addWidget(self.session_card)
        self.editor_card = QFrame()
        self.editor_card.setObjectName("editor_card")
        editor = QVBoxLayout(self.editor_card)
        self.editor_layout = editor
        editor.setContentsMargins(20, 20, 20, 20)
        editor.setSpacing(0)
        editor_heading = QHBoxLayout()
        editor_heading.setSpacing(20)
        self.editor_status_dot = QFrame()
        self.editor_status_dot.setObjectName("status_dot")
        self.editor_title = QLabel("새 세션")
        self.editor_title.setObjectName("rds_editor_title")
        self.editor_title.setTextFormat(Qt.TextFormat.PlainText)
        self.editor_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.editor_subtitle = QLabel("연결 정보를 입력하고 저장하세요.")
        self.editor_subtitle.setObjectName("page_subtitle")
        editor_title_block = QVBoxLayout()
        editor_title_block.setSpacing(4)
        editor_title_block.addWidget(self.editor_title)
        editor_title_block.addWidget(self.editor_subtitle)
        editor_heading.addWidget(self.editor_status_dot)
        editor_heading.addLayout(editor_title_block, 1)
        self.rename_button = QPushButton()
        self.rename_button.setObjectName("rds_rename")
        self.rename_button.setProperty("action_button", True)
        set_button_icon(self.rename_button, "common-edit.svg", tooltip="터널 이름 변경")
        self.rename_button.clicked.connect(self.rename_selected)
        editor_heading.addWidget(self.rename_button)
        editor.addLayout(editor_heading)
        details_widget = QWidget()
        details_widget.setObjectName("rds_editor_details")
        details = QVBoxLayout(details_widget)
        self.details_layout = details
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(0)
        details.setAlignment(Qt.AlignmentFlag.AlignTop)
        form = QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(14)
        form.setContentsMargins(0, 18, 0, 0)
        self.name = QLineEdit()
        self.name.setObjectName("rds_name")
        self.host = QLineEdit()
        self.host.setObjectName("rds_host")
        self.host_catalog = QComboBox()
        self.host_catalog.setObjectName("rds_host_catalog")
        self.host_catalog.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.host_catalog.setMinimumContentsLength(20)
        self.host_catalog.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.host_catalog.setVisible(False)
        self.host_catalog.currentIndexChanged.connect(self._endpoint_selected)
        self.remote_port = QSpinBox()
        self.remote_port.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.remote_port.setRange(1, 65535)
        self.remote_port.setValue(3306)
        self.local_port = QSpinBox()
        self.local_port.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.local_port.setRange(1, 65535)
        self.local_port.setValue(13306)
        self.relay = QComboBox()
        self.relay.setObjectName("rds_relay")
        self.relay.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.relay.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.relay.currentIndexChanged.connect(self._sync_editor_actions)
        self.name_field = _field("세션명", self.name)
        form.addWidget(self.name_field, 0, 0, 1, 2)
        form.addWidget(
            _field(
                "SSM 중계 EC2",
                self.relay,
                help_text="RDS에 접근하여 SSM 포트포워딩을 실행할 EC2입니다.",
            ),
            1,
            0,
            1,
            2,
        )
        host_controls = QWidget()
        host_controls.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        host_layout = QVBoxLayout(host_controls)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(6)
        host_layout.addWidget(self.host_catalog)
        host_layout.addWidget(self.host)
        form.addWidget(
            _field(
                "RDS 엔드포인트",
                host_controls,
                help_text="중계 EC2에서 연결할 RDS의 호스트 주소입니다.",
            ),
            2,
            0,
            1,
            2,
        )
        form.addWidget(_field("RDS Port", self.remote_port), 3, 0)
        port_controls = QWidget()
        port_layout = QHBoxLayout(port_controls)
        port_layout.setContentsMargins(0, 0, 0, 0)
        port_layout.setSpacing(10)
        self.port_status = QLabel()
        self.port_status.setObjectName("rds_port_status")
        self.port_status.setFixedWidth(24)
        port_layout.addWidget(self.local_port, 1)
        port_layout.addWidget(self.port_status)
        form.addWidget(
            _field(
                "로컬 포트",
                port_controls,
                help_text="로컬 PC의 127.0.0.1에서 열릴 포트입니다. 빈 포트를 입력하세요.",
            ),
            3,
            1,
        )
        form.setColumnStretch(0, 1)
        form.setColumnStretch(1, 1)
        details.addLayout(form)
        self.relay_status = QLabel("중계 EC2 목록을 불러오기 전에는 저장할 수 없습니다.")
        self.relay_status.setObjectName("rds_relay_status")
        self.relay_status.setWordWrap(True)
        details.addWidget(self.relay_status)
        details.addSpacing(16)
        self.tunnel_state = QLabel("중지됨")
        self.tunnel_state.setObjectName("rds_tunnel_state")
        self.tunnel_state.setWordWrap(True)
        self.tunnel_state.setTextFormat(Qt.TextFormat.PlainText)
        self.tunnel_state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.copy_address_button = QToolButton()
        self.copy_address_button.setText("접속 정보 복사")
        self.copy_address_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.copy_address_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.copy_address_button.setObjectName("rds_copy_address")
        set_button_icon(
            self.copy_address_button,
            "common-clipboard.svg",
            tooltip="접속 정보 복사",
            accessible_name="접속 정보 복사",
        )
        self.copy_address_button.setEnabled(False)
        self.copy_address_button.clicked.connect(self.copy_selected_address)
        copy_menu = QMenu(self.copy_address_button)
        for label, copy_format in (
            ("Host: 127.0.0.1", "host"),
            ("Port", "port"),
            ("127.0.0.1:포트", "address"),
            ("전체 매핑 문자열", "mapping"),
        ):
            action = copy_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=copy_format: self.copy_connection_info(value)
            )
        self.copy_address_button.setMenu(copy_menu)
        self.notice = QFrame()
        self.notice.setObjectName("rds_connection_card")
        self.notice.setMinimumHeight(46)
        notice_layout = QHBoxLayout(self.notice)
        notice_layout.setContentsMargins(12, 6, 12, 6)
        notice_layout.setSpacing(10)
        self.connection_icon = QLabel()
        self.connection_icon.setObjectName("rds_connection_icon")
        self.connection_icon.setPixmap(
            gui_icon("link-solid-full.svg", color="#2563eb", size=20).pixmap(20, 20)
        )
        self.connection_label = QLabel("연결됨")
        self.connection_label.setObjectName("rds_connection_label")
        notice_layout.addWidget(self.connection_icon)
        notice_layout.addWidget(self.connection_label)
        notice_layout.addWidget(self.tunnel_state, 1)
        self.connection_icon.hide()
        self.connection_label.hide()
        details.addWidget(self.notice)
        self.running_notice = icon_text(
            "common-info.svg",
            "터널이 실행 중인 동안에는 연결 정보를 수정할 수 없습니다. "
            "변경하려면 터널을 먼저 중지해주세요.",
            color="#00a166",
        )
        self.running_notice.setObjectName("rds_running_notice")
        info_label = self.running_notice.findChild(QLabel, "icon_text_label")
        assert info_label is not None
        info_label.setWordWrap(True)
        info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        info_layout = self.running_notice.layout()
        assert isinstance(info_layout, QHBoxLayout)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setStretch(1, 1)
        details.addSpacing(12)
        details.addWidget(self.running_notice)
        self.running_notice.hide()
        self.copy_notice = QLabel()
        self.copy_notice.setObjectName("rds_copy_notice")
        details_scroll = QScrollArea()
        details_scroll.setObjectName("rds_editor_details_scroll")
        details_scroll.setWidgetResizable(True)
        details_scroll.setWidget(details_widget)
        editor.addWidget(details_scroll, 1)
        self.editor_actions_container = QWidget()
        self.editor_actions_container.setObjectName("rds_editor_actions")
        actions = QGridLayout(self.editor_actions_container)
        actions.setContentsMargins(0, 20, 0, 0)
        actions.setHorizontalSpacing(8)
        self.delete_button = QPushButton()
        self.delete_button.setProperty("variant", "danger")
        self.clone_button = QPushButton()
        self.save_button = QPushButton()
        self.connection_button = QPushButton()
        self.connection_button.setObjectName("rds_connection")
        for button, icon, label, color in (
            (self.delete_button, "common-delete.svg", "삭제", "#ffffff"),
            (self.clone_button, "common-copy.svg", "복제", None),
            (self.save_button, "common-save.svg", "저장", None),
            (self.connection_button, "common-start.svg", "연결 시작", "#ffffff"),
        ):
            set_button_icon(button, icon, tooltip=label, color=color)
        # Transitional aliases keep callers source-compatible while one actual button is rendered.
        self.start_button = self.connection_button
        self.stop_button = self.connection_button
        for button in (
            self.clone_button,
            self.delete_button,
            self.save_button,
            self.connection_button,
        ):
            button.setProperty("action_button", True)
        self.delete_button.clicked.connect(self.delete_selected)
        self.clone_button.clicked.connect(self.clone_selected)
        self.save_button.clicked.connect(self.save)
        self.connection_button.clicked.connect(self.toggle_connection)
        self.delete_button.setParent(self)
        self.clone_button.setParent(self)
        self.delete_button.hide()
        self.clone_button.hide()
        self.editor_actions = actions
        self._layout_editor_actions()
        details.addWidget(self.editor_actions_container)
        details.addWidget(self.copy_notice)
        details.addStretch()
        self._actions_outside_scroll = False
        content.addWidget(self.editor_card, 1)
        root.addLayout(content, 1)
        # Active rows are projected to the dashboard; the page itself uses the selected session.
        self.active_list = QListWidget()
        self.active_list.setObjectName("active_tunnels")
        self.active_list.hide()
        self.active_list.currentItemChanged.connect(self._active_selection_changed)
        root.addWidget(self.active_list)
        self.new_session(confirm_discard=False, add_draft=False)

    def set_profile(self, profile_id: int | None) -> None:
        if self._profile_id == profile_id:
            return
        self._profile_id = profile_id
        self._port_revision += 1
        self._saved.clear()
        self._active.clear()
        self._relays_ready = False
        self.relay.clear()
        self.relay_status.setText("중계 EC2 목록을 불러오는 중입니다…")
        self.new_session(confirm_discard=False, add_draft=False)
        if profile_id is not None:
            self.reload()
            self.reload_relays()
            self.reload_endpoints()
            self.refresh_active()

    def reload_endpoints(self) -> None:
        endpoints = self._endpoints
        if self._profile_id is None or endpoints is None:
            self.host_catalog.hide()
            self.host.show()
            return
        profile_id = self._profile_id
        self._runner.submit(
            lambda: endpoints.list(profile_id),
            self._endpoints_loaded,
            self._endpoints_failed,
        )

    def _endpoints_loaded(self, value: Any) -> None:
        current = self.host.text().strip()
        self.host_catalog.clear()
        for endpoint in value:
            if isinstance(endpoint, RdsEndpoint):
                self.host_catalog.addItem(
                    f"{endpoint.identifier} · {endpoint.engine} · {endpoint.host}:{endpoint.port}",
                    endpoint,
                )
        self.host_catalog.setVisible(self.host_catalog.count() > 0)
        self.host.setVisible(self.host_catalog.count() == 0)
        index = next(
            (
                item
                for item in range(self.host_catalog.count())
                if self.host_catalog.itemData(item).host == current
            ),
            -1,
        )
        if index >= 0:
            self.host_catalog.setCurrentIndex(index)
        elif self.host_catalog.count() > 0:
            self._endpoint_selected(0)

    def _endpoints_failed(self, error: ApplicationError) -> None:
        self.host_catalog.hide()
        self.host.show()
        if isinstance(error, AwsPermissionError):
            self.notice_raised.emit("RDS 목록 권한이 없어 Host 직접 입력을 유지합니다.")
            return
        self.error_raised.emit(error)

    def _endpoint_selected(self, index: int) -> None:
        endpoint = self.host_catalog.itemData(index) if index >= 0 else None
        if isinstance(endpoint, RdsEndpoint):
            self.host.setText(endpoint.host)
            self.remote_port.setValue(endpoint.port)

    def reload(self) -> None:
        if self._profile_id is None:
            return
        self._runner.submit(
            lambda: self._sessions.list(self._profile_id), self._sessions_loaded, self._failed
        )

    def _sessions_loaded(self, value: Any) -> None:
        self._saved = {item.require_id(): item for item in value}
        self._render_sessions()

    def _render_sessions(self) -> None:
        query = self.search.text().strip().casefold()
        # Polling updates list status without re-emitting selection and overwriting
        # unsaved editor values such as a newly chosen Local Port.
        with QSignalBlocker(self.session_list):
            self.session_list.clear()
            if self._draft_visible:
                draft = QListWidgetItem()
                draft.setData(Qt.ItemDataRole.UserRole, None)
                self.session_list.addItem(draft)
                set_compact_list_row(
                    self.session_list,
                    draft,
                    "새 세션",
                    ("저장되지 않음",),
                    show_status=True,
                    trailing=self._session_menu_button(None),
                )
                if self._selected_id is None:
                    self.session_list.setCurrentItem(draft)
            for session in self._saved.values():
                if query and query not in f"{session.name} {session.host}".casefold():
                    continue
                is_active = any(active.tunnel.id == session.id for active in self._active.values())
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, session.require_id())
                self.session_list.addItem(item)
                set_compact_list_row(
                    self.session_list,
                    item,
                    session.name,
                    (f"localhost:{session.local_port} · {'연결됨' if is_active else '중지됨'}",),
                    connected=is_active,
                    show_status=True,
                    trailing=self._session_menu_button(session.require_id()),
                )
                if session.id == self._selected_id:
                    self.session_list.setCurrentItem(item)
            update_list_row_separators(self.session_list)

    def reload_relays(self) -> None:
        if self._profile_id is not None:
            self._relays_ready = False
            self.relay.setEnabled(False)
            self.relay_status.setText("중계 EC2 목록을 불러오는 중입니다…")
            self._sync_editor_actions()
            profile_id = self._profile_id

            def action() -> list[Ec2Target]:
                return self._ec2.list_targets(profile_id)

            if self._authenticated is None:
                self._runner.submit(action, self._relays_loaded, self._failed)
            else:
                self._authenticated.submit(
                    profile_id,
                    action,
                    self._relays_loaded,
                    self._failed,
                    lambda: self.tunnel_state.setText("MFA 인증 취소"),
                )

    def _relays_loaded(self, value: Any) -> None:
        current = self.relay.currentData()
        self.relay.clear()
        for target in value:
            self.relay.addItem(
                f"{target.name or '이름 없음'} · {target.instance_id}", target.instance_id
            )
        index = self.relay.findData(current)
        if index >= 0:
            self.relay.setCurrentIndex(index)
        self._relays_ready = True
        self.relay.setEnabled(self.relay.count() > 0)
        self.relay_status.setText(
            ""
            if self.relay.count() > 0
            else "연결 가능한 중계 EC2가 없어 저장하거나 연결을 시작할 수 없습니다."
        )
        self.relay_status.setVisible(self.relay.count() == 0)
        if self._selected_id is None:
            self._editor_baseline = self._editor_values()
        self._sync_editor_actions()

    def new_session(self, *, confirm_discard: bool = True, add_draft: bool = True) -> None:
        if confirm_discard and self._is_editor_dirty() and not self._discard_confirmation(self):
            return
        self._selected_id = None
        self._draft_visible = add_draft
        self.name_field.show()
        self.session_list.clearSelection()
        self.editor_title.setText("새 세션")
        self.editor_subtitle.setText("연결 정보를 입력하고 저장하세요.")
        self.name.clear()
        self.host.clear()
        self.remote_port.setValue(3306)
        self.local_port.setValue(13306)
        self._render_sessions()
        self._editor_baseline = self._editor_values()
        self._check_local_port()
        self._sync_editor_actions()

    def prefill_endpoint(self, host: str, remote_port: int) -> None:
        """Populate only approved endpoint fields; persistence remains an explicit save."""

        self.new_session(confirm_discard=False)
        self.host.setText(host)
        self.remote_port.setValue(remote_port)
        self.name.setFocus()

    def _select_session(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        selected_data = current.data(Qt.ItemDataRole.UserRole)
        if selected_data is None:
            self.new_session(confirm_discard=False)
            return
        session_id = int(selected_data)
        session = self._saved[session_id]
        self._selected_id = session_id
        self.name_field.hide()
        self.editor_title.setText(session.name)
        self.editor_subtitle.setText("저장된 터널 세션")
        self.name.setText(session.name)
        self.host.setText(session.host)
        self.remote_port.setValue(session.remote_port)
        self.local_port.setValue(session.local_port)
        if session.target_instance_id:
            index = self.relay.findData(session.target_instance_id)
            if index >= 0:
                self.relay.setCurrentIndex(index)
        self._editor_baseline = self._editor_values()
        self._check_local_port()
        self._sync_editor_actions()

    def _session_menu_button(self, session_id: int | None) -> QPushButton:
        button = QPushButton()
        button.setObjectName("rds_session_more")
        button.setProperty("action_button", True)
        set_button_icon(button, "common-more.svg", tooltip="터널 더보기")
        button.clicked.connect(lambda: self._show_session_menu(session_id, button))
        return button

    def _show_session_menu(self, session_id: int | None, button: QPushButton) -> None:
        for row in range(self.session_list.count()):
            item = self.session_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == session_id:
                self.session_list.setCurrentItem(item)
                break
        menu = QMenu(button)
        menu.setObjectName("rds_session_menu")
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        for text, icon, action in (
            ("터널 이름 변경", "common-edit.svg", self.rename_selected),
            ("세션 복제", "common-copy.svg", self.clone_selected),
        ):
            entry = menu.addAction(gui_icon(icon), text)
            entry.setEnabled(session_id is not None and not self._connection_busy)
            entry.triggered.connect(action)
        delete = QWidgetAction(menu)
        delete.setText("세션 삭제")
        delete_button = QPushButton("세션 삭제")
        delete_button.setObjectName("rds_menu_delete")
        set_button_icon(delete_button, "common-delete.svg", color="#d92d20")
        delete.setDefaultWidget(delete_button)
        delete.setEnabled(self._selected_operation_id() is None and not self._connection_busy)
        delete.triggered.connect(self.delete_selected)
        delete_button.clicked.connect(delete.trigger)
        delete_button.clicked.connect(menu.close)
        menu.addAction(delete)
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def rename_selected(self) -> None:
        if self._selected_id is None or self._profile_id is None or self._connection_busy:
            return
        session_id, profile_id = self._selected_id, self._profile_id
        source = self._saved[session_id]
        name, confirmed = self._rename_name_provider(self, source.name)
        if not confirmed:
            return

        def renamed(session: TunnelSession) -> None:
            if self._profile_id != profile_id:
                return
            self._saved[session_id] = session
            if self._selected_id == session_id:
                self.name.setText(session.name)
                self.editor_title.setText(session.name)
                if self._editor_baseline is not None:
                    self._editor_baseline = (session.name, *self._editor_baseline[1:])
            self._render_sessions()
            self.notice_raised.emit("세션 이름을 변경했습니다.")

        self._runner.submit(
            lambda: self._sessions.rename(session_id, name.strip(), profile_id),
            renamed,
            self._failed,
        )

    def _schedule_port_check(self) -> None:
        self._port_revision += 1
        self._port_available = None
        self._sync_editor_actions()
        self._port_timer.start()

    def _check_local_port(self) -> None:
        self._port_timer.stop()
        self._port_revision += 1
        revision = self._port_revision
        self._port_available = None
        if self._profile_id is None or self._selected_operation_id() is not None:
            self._sync_editor_actions()
            return
        port = self.local_port.value()
        self._sync_editor_actions()

        def loaded(available: bool) -> None:
            if revision == self._port_revision and self.local_port.value() == port:
                self._port_available = available
                self._sync_editor_actions()

        def failed(error: ApplicationError) -> None:
            if revision == self._port_revision:
                self._port_available = False
                self._failed(error)

        self._runner.submit(lambda: self._tunnels.local_port_available(port), loaded, failed)

    def _render_port_status(self, running: bool) -> None:
        available = running or self._port_available
        if available is None:
            self.port_status.clear()
            self.port_status.setToolTip("로컬 포트 확인 중…")
            return
        self.port_status.setPixmap(
            gui_icon(
                "common-circle-check.svg" if available else "common-circle-exclamation.svg",
                color="#00a166" if available else "#d92d20",
                size=24,
            ).pixmap(24, 24)
        )
        message = (
            "현재 터널이 사용 중인 포트입니다."
            if running
            else (
                "사용 가능한 포트입니다."
                if available
                else "이미 사용 중이거나 사용할 수 없는 포트입니다."
            )
        )
        self.port_status.setToolTip(message)
        self.port_status.setAccessibleName(message)

    def clone_selected(self) -> None:
        """Confirm a new name, then create a separate saved-session record."""

        if self._selected_id is None or self._profile_id is None:
            return
        selected_id = self._selected_id
        profile_id = self._profile_id
        source = self._saved[selected_id]
        name, confirmed = self._clone_name_provider(self, source.name)
        if not confirmed:
            return
        self._runner.submit(
            lambda: self._sessions.clone(selected_id, name.strip(), profile_id),
            self._cloned_done,
            self._failed,
        )

    def _cloned_done(self, _value: Any) -> None:
        self.notice_raised.emit("RDS 터널 세션을 복제했습니다.")
        self.new_session(confirm_discard=False, add_draft=False)
        self.reload()

    def _save_request(self) -> SaveTunnelSessionRequest | None:
        if self._profile_id is None or not self._relay_available():
            return None
        relay = self.relay.currentData()
        return SaveTunnelSessionRequest(
            self._profile_id,
            self.name.text().strip(),
            self.host.text().strip(),
            self.remote_port.value(),
            self.local_port.value(),
            TargetMode.FIXED,
            str(relay),
            self._selected_id,
        )

    def save(self) -> None:
        if self._selected_operation_id() is not None or self._connection_busy:
            return
        request = self._save_request()
        if request is None:
            return
        self._persist(request, self._saved_done)

    def _persist(
        self,
        request: SaveTunnelSessionRequest,
        completed: Callable[[TunnelSession], None],
    ) -> None:

        def save_operation() -> TunnelSession:
            if request.tunnel_id is not None:
                return self._sessions.update(request)
            return self._sessions.create(request)

        self._runner.submit(save_operation, completed, self._failed)

    def _saved_done(self, session: TunnelSession) -> None:
        self._selected_id = session.require_id()
        self._draft_visible = False
        self.name_field.hide()
        self.editor_title.setText(session.name)
        self._sync_editor_actions()
        self.notice_raised.emit("RDS 터널 세션을 저장했습니다.")
        self._editor_baseline = self._editor_values()
        self.reload()

    def delete_selected(self) -> None:
        if self._selected_id is None and self._draft_visible:
            if self._is_editor_dirty() and not self._discard_confirmation(self):
                return
            self.new_session(confirm_discard=False, add_draft=False)
            if self.session_list.count():
                self.session_list.setCurrentRow(0)
            return
        if self._selected_id is None or self._profile_id is None:
            return
        if self._selected_operation_id() is not None or self._connection_busy:
            return
        if not self._delete_confirmation(self, self._selected_id):
            return
        selected = self._selected_id
        self._runner.submit(
            lambda: self._sessions.delete(selected, self._profile_id),
            lambda _value: self._deleted_done(),
            self._failed,
        )

    def _deleted_done(self) -> None:
        self.notice_raised.emit("RDS 터널 세션을 삭제했습니다.")
        self.new_session(confirm_discard=False, add_draft=False)
        self.reload()

    def start(self, tunnel_id: int | None = None) -> None:
        if (
            self._connection_busy
            or (tunnel_id is None and self._selected_id is None)
            or self._profile_id is None
            or not self._relay_available()
            or self._port_available is not True
        ):
            return
        selected_id = tunnel_id if tunnel_id is not None else self._selected_id
        if selected_id is None:
            return
        session = self._saved.get(selected_id)
        selected_value = self.relay.currentData()
        selected = (
            str(selected_value)
            if session is not None
            and session.target_mode is TargetMode.SELECT
            and selected_value is not None
            else None
        )
        request = StartTunnelRequest(selected_id, self._profile_id, selected)
        self._connection_busy = True
        self._sync_editor_actions()
        self.tunnel_state.setText("연결 시작 중…")
        self._runner.submit(
            lambda: self._tunnels.start_managed(request),
            lambda result: self._start_result(result, request),
            self._failed,
        )

    def _start_result(
        self,
        result: OperationResult[TunnelConnectionResult],
        request: StartTunnelRequest,
    ) -> None:
        if result.state is OperationState.MFA_REQUIRED and result.challenge is not None:
            code = self._mfa_code_provider(self, result.challenge.device_arn)
            self._runner.submit(
                lambda: self._tunnels.resume(result.operation_id, code),
                lambda resumed: self._start_result(resumed, request),
                self._failed,
            )
            return
        self._connection_busy = False
        if result.state is OperationState.SUCCEEDED and result.value is not None:
            address = f"127.0.0.1:{result.value.tunnel.local_port}"
            self.tunnel_state.setText(
                f"연결됨 · {address} → {result.value.tunnel.host}:{result.value.tunnel.remote_port}"
            )
            self.copy_address_button.setProperty("local_address", address)
            self.copy_address_button.setEnabled(True)
            self.notice_raised.emit("RDS 터널을 시작했습니다.")
            self.refresh_active()
            self._poll_timer.start()
        elif result.state is OperationState.CANCELLED:
            self.tunnel_state.setText("MFA 인증 취소")
        elif result.error is not None:
            self._failed(result.error)
        self._sync_editor_actions()

    def refresh_active(self) -> None:
        profile_id = self._profile_id
        self._runner.submit(
            lambda: self._tunnels.active_tunnels(profile_id),
            self._active_loaded,
            self._failed,
        )

    def _active_loaded(self, value: Any) -> None:
        was_running = self._selected_operation_id() is not None
        observed = list(value)
        terminal = [item for item in observed if item.handle.state is not OperationState.RUNNING]
        active = [item for item in observed if item.handle.state is OperationState.RUNNING]
        self._active = {
            item.handle.operation_id: item
            for item in active
            if item.handle.operation_id is not None
        }
        self._render_sessions()
        self.active_list.clear()
        summaries: list[ActiveTunnelSummaryViewModel] = []
        for operation_id, item in self._active.items():
            summary = ActiveTunnelSummaryViewModel(
                item.tunnel.name,
                item.tunnel.local_port,
                item.tunnel.host,
                item.tunnel.remote_port,
                operation_id,
            )
            row = QListWidgetItem()
            row.setData(Qt.ItemDataRole.UserRole, operation_id)
            self.active_list.addItem(row)
            widget = ActiveTunnelRow(summary)
            widget.copied.connect(self._address_copied)
            widget.stop_requested.connect(self.stop_operation)
            row.setSizeHint(widget.sizeHint())
            self.active_list.setItemWidget(row, widget)
            summaries.append(summary)
        if self.active_list.count() and self.active_list.currentRow() < 0:
            self.active_list.setCurrentRow(0)
        self.tunnels_changed.emit(summaries)
        if terminal:
            failed = next((item for item in terminal if item.error is not None), None)
            if failed is not None:
                self.tunnel_state.setText("터널 프로세스가 비정상 종료되었습니다.")
                self.error_raised.emit(failed.error)
            elif not active:
                self.tunnel_state.setText("중지됨")
            if not active:
                self._poll_timer.stop()
                self.copy_address_button.setEnabled(False)
        elif not active:
            self._poll_timer.stop()
            self.tunnel_state.setText("중지됨")
            self.copy_address_button.setEnabled(False)
        if was_running and self._selected_operation_id() is None:
            self._check_local_port()
        self._sync_editor_actions()

    def _active_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        self._sync_editor_actions()

    def _connection_summary(self) -> ActiveTunnelSummaryViewModel | None:
        operation_id = self._selected_operation_id()
        active = self._active.get(operation_id) if operation_id is not None else None
        session = active.tunnel if active is not None else self._saved.get(self._selected_id or -1)
        if session is None:
            return None
        return ActiveTunnelSummaryViewModel(
            session.name, session.local_port, session.host, session.remote_port, operation_id
        )

    def copy_selected_address(self) -> None:
        self.copy_connection_info("host_port")

    def copy_connection_info(self, copy_format: str) -> None:
        summary = self._connection_summary()
        if summary is None:
            return
        text = summary.connection_copy_text(copy_format)
        copy_temporarily(text)
        self._address_copied(text)

    def _address_copied(self, _address: str) -> None:
        self.copy_notice.setText("접속 정보가 복사되었습니다.")
        self.notice_raised.emit("접속 정보가 복사되었습니다.")

    def stop_selected(self) -> None:
        operation_id = self._selected_operation_id()
        if operation_id is None:
            return
        self.stop_operation(operation_id)

    def toggle_connection(self) -> None:
        """Run the sole connection action for the selected saved session."""

        operation_id = self._selected_operation_id()
        if operation_id is None:
            if self._port_available is not True or self._connection_busy:
                return
            if self._is_editor_dirty():
                request = self._save_request()
                if request is not None:
                    self._connection_busy = True
                    self._sync_editor_actions()
                    self.tunnel_state.setText("변경사항 저장 중…")
                    self._persist(request, self._saved_then_start)
            else:
                self.start()
        else:
            self.stop_operation(operation_id)

    def _saved_then_start(self, session: TunnelSession) -> None:
        self._selected_id = session.require_id()
        self._saved[self._selected_id] = session
        self._editor_baseline = self._editor_values()
        self._connection_busy = False
        self.notice_raised.emit("변경사항을 저장하고 RDS 터널 연결을 시작합니다.")
        self.start(self._selected_id)

    def stop_operation(self, operation_id: str) -> None:
        """Stop an owned tunnel selected from either the RDS page or dashboard."""

        if self._connection_busy:
            return
        self._connection_busy = True
        self._sync_editor_actions()
        self._runner.submit(
            lambda: self._tunnels.stop(operation_id),
            self._stop_result,
            self._failed,
        )

    def _stop_result(self, value: Any) -> None:
        result = value
        if result.state is OperationState.FAILED and result.error is not None:
            self._failed(result.error)
        elif result.state is OperationState.CANCELLED:
            self._stopped()
        else:
            self._stopped()

    def _stopped(self) -> None:
        self._connection_busy = False
        self.notice_raised.emit("RDS 터널을 종료했습니다.")
        self.refresh_active()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        """Keep the mockup split while protecting the editor action row."""

        compact = self.width() < 900
        self.session_card.setFixedWidth(230 if compact else 310)
        self._place_editor_actions(outside_scroll=compact)
        super().resizeEvent(event)

    def shutdown(self, completed: Callable[[], None]) -> None:
        self._poll_timer.stop()
        self._runner.submit(
            self._tunnels.stop_all,
            lambda _value: completed(),
            lambda error: self._shutdown_failed(error, completed),
        )

    def _shutdown_failed(self, error: ApplicationError, completed: Callable[[], None]) -> None:
        self.error_raised.emit(error)
        completed()

    def _failed(self, error: ApplicationError) -> None:
        self._connection_busy = False
        self._sync_editor_actions()
        self.tunnel_state.setText("작업 실패 · 앱은 계속 실행됩니다.")
        self.error_raised.emit(error)

    def _selected_operation_id(self) -> str | None:
        if self._selected_id is None:
            return None
        return next(
            (
                operation_id
                for operation_id, active in self._active.items()
                if active.tunnel.id == self._selected_id
            ),
            None,
        )

    def _relay_available(self) -> bool:
        return (
            self._relays_ready and self.relay.count() > 0 and self.relay.currentData() is not None
        )

    def _sync_editor_actions(self) -> None:
        selected = self._selected_id is not None
        relay_available = self._relay_available()
        operation_id = self._selected_operation_id()
        self.delete_button.setEnabled(
            (selected or self._draft_visible) and not self._connection_busy
        )
        self.clone_button.setEnabled(selected and not self._connection_busy)
        self.save_button.setEnabled(
            self._profile_id is not None and relay_available and not self._connection_busy
        )
        set_button_icon(
            self.connection_button,
            "common-stop.svg" if operation_id is not None else "common-start.svg",
            tooltip="연결 종료" if operation_id is not None else "연결 시작",
            color="#ffffff",
        )
        self.connection_button.setProperty(
            "variant", "danger" if operation_id is not None else "primary"
        )
        self.connection_button.style().unpolish(self.connection_button)
        self.connection_button.style().polish(self.connection_button)
        self.connection_button.setEnabled(
            selected and relay_available and not self._connection_busy
        )
        self.editor_status_dot.setProperty("active", operation_id is not None)
        running = operation_id is not None
        editable = not running and not self._connection_busy
        for control in (self.name, self.host, self.host_catalog, self.remote_port, self.local_port):
            control.setEnabled(editable)
        self.relay.setEnabled(editable and relay_available)
        self.save_button.setEnabled(self._profile_id is not None and relay_available and editable)
        self.delete_button.setEnabled((selected or self._draft_visible) and editable)
        self.rename_button.setEnabled(selected and not self._connection_busy)
        self.connection_button.setEnabled(
            not self._connection_busy
            and (running or (selected and relay_available and self._port_available is True))
        )
        self.running_notice.setVisible(running)
        self.connection_icon.setVisible(running)
        self.connection_label.setVisible(running)
        self.editor_status_dot.show()
        self.editor_status_dot.style().unpolish(self.editor_status_dot)
        self.editor_status_dot.style().polish(self.editor_status_dot)
        summary = self._connection_summary()
        self.copy_address_button.setEnabled(summary is not None)
        if not self._connection_busy:
            self.tunnel_state.setText(
                f"· {summary.connection_copy_text('mapping')}"
                if running and summary is not None
                else "중지됨 · 연결되지 않았습니다."
            )
        self._render_port_status(running)
        if selected:
            self.editor_subtitle.setText(
                "현재 터널이 실행 중입니다."
                if operation_id is not None
                else "저장된 터널 세션 · 중지됨"
            )

    def _layout_editor_actions(self) -> None:
        self.editor_actions.addWidget(self.copy_address_button, 0, 0)
        self.editor_actions.setColumnStretch(1, 1)
        self.editor_actions.addWidget(self.save_button, 0, 2)
        self.editor_actions.addWidget(self.connection_button, 0, 3)

    def _place_editor_actions(self, *, outside_scroll: bool) -> None:
        if self._actions_outside_scroll == outside_scroll:
            return
        if outside_scroll:
            self.details_layout.removeWidget(self.editor_actions_container)
            self.editor_layout.addWidget(self.editor_actions_container)
        else:
            self.editor_layout.removeWidget(self.editor_actions_container)
            self.details_layout.insertWidget(
                max(0, self.details_layout.count() - 1), self.editor_actions_container
            )
        self._actions_outside_scroll = outside_scroll

    def _editor_values(self) -> tuple[object, ...]:
        return (
            self.name.text(),
            self.host.text(),
            self.remote_port.value(),
            self.local_port.value(),
            self.relay.currentData(),
        )

    def _is_editor_dirty(self) -> bool:
        return self._editor_baseline is not None and self._editor_values() != self._editor_baseline


def _ask_rename_name(parent: QWidget, source_name: str) -> tuple[str, bool]:
    return QInputDialog.getText(parent, "터널 이름 변경", "새 터널 이름", text=source_name)


def _ask_clone_name(parent: QWidget, source_name: str) -> tuple[str, bool]:
    return QInputDialog.getText(
        parent,
        "RDS 터널 세션 복제",
        "새 세션명",
        text=f"{source_name} 복사본",
    )


def _confirm_discard(parent: QWidget) -> bool:
    return (
        QMessageBox.question(
            parent,
            "미저장 변경사항",
            "입력한 내용을 버리고 새 세션을 만드시겠습니까?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        is QMessageBox.StandardButton.Discard
    )


def _field(label: str, control: QWidget, *, help_text: str | None = None) -> QWidget:
    container = QWidget()
    container.setObjectName("field")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    caption = QLabel(label)
    caption.setObjectName("field_label")
    if help_text is None:
        layout.addWidget(caption)
    else:
        caption_row = QHBoxLayout()
        caption_row.addWidget(caption)
        help_icon = QToolButton()
        help_icon.setObjectName("field_help")
        set_button_icon(help_icon, "common-info.svg", tooltip=help_text, color="#7b88a5", size=16)
        caption_row.addWidget(help_icon)
        caption_row.addStretch()
        layout.addLayout(caption_row)
    layout.addWidget(control)
    return container


def _status_label(text: str, status: str, *, icon: str | None = None) -> QWidget:
    if icon is not None:
        badge = icon_text(icon, text, color="#00a166" if status == "success" else "#d92d20")
        badge_layout = badge.layout()
        if badge_layout is not None:
            badge_layout.setContentsMargins(6, 0, 6, 0)
            badge_layout.setSpacing(4)
        badge.setProperty("status", status)
        badge.setFixedHeight(28)
        return badge
    label = QLabel(text)
    label.setProperty("status", status)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def _centered_cell(control: QWidget) -> QWidget:
    cell = QWidget()
    layout = QHBoxLayout(cell)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.addWidget(control, alignment=Qt.AlignmentFlag.AlignCenter)
    return cell


def _instance_state_label(state: str | None) -> str:
    if state == "running":
        return "● 실행 중"
    if state == "stopped":
        return "● 중지됨"
    return "—"


def _target_action_text(target: Ec2Target) -> str:
    if target.instance_state == "stopped":
        return "인스턴스 실행"
    if not target.ssm_ready:
        return "SSM 연결을 지원하지 않거나 현재 사용할 수 없습니다."
    return "터미널 열기"


def _target_action_role(target: Ec2Target) -> str:
    return "primary" if target.ssm_ready and target.instance_state != "stopped" else "default"


def _target_action_enabled(target: Ec2Target) -> bool:
    if target.instance_state == "stopped":
        return target.power_actions_available
    return target.ssm_ready and target.instance_state in {None, "running"}
