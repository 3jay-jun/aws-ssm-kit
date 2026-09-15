"""EC2 and RDS GUI adapters backed exclusively by Application Services."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
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
from aws_connect.presentation.gui.icons import gui_icon
from aws_connect.presentation.gui.table_selection import use_first_column_selection_bar
from aws_connect.presentation.gui.tasks import GuiTaskRunner
from aws_connect.presentation.gui.view_models import ActiveTunnelSummaryViewModel

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
        copy_button.clicked.connect(self._copy)
        layout.addWidget(copy_button)
        stop_button = QPushButton("연결 종료")
        stop_button.setProperty("size", "small")
        stop_button.setProperty("variant", "danger")
        stop_button.setAccessibleName(f"{summary.name} 터널 종료")
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
        self.refresh_button = QPushButton("↻ 목록 새로고침")
        self.refresh_button.setObjectName("ec2_refresh")
        self.refresh_button.clicked.connect(self.reload)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        layout.addSpacing(8)
        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.region = QLineEdit(self._region)
        self.region.setObjectName("ec2_region")
        self.region.setPlaceholderText("ap-northeast-2")
        self.region.setAccessibleName("EC2 Region")
        self.status = QComboBox()
        self.status.setObjectName("ec2_status_filter")
        self.status.setAccessibleName("인스턴스 상태")
        self.status.addItem("전체", "all")
        self.status.addItem("실행 중", "running")
        self.status.addItem("중지됨", "stopped")
        self.filter = QLineEdit()
        self.filter.setObjectName("ec2_filter")
        self.filter.setPlaceholderText("이름, Instance ID, Private IP")
        self.filter.textChanged.connect(self._render_targets)
        self.status.currentIndexChanged.connect(self._render_targets)
        self.region.editingFinished.connect(self.reload)
        filters.addWidget(_field("Region", self.region))
        filters.addWidget(_field("상태", self.status))
        filters.addWidget(_field("검색", self.filter), 1)
        layout.addLayout(filters)
        self.table_card = QFrame()
        self.table_card.setObjectName("content_card")
        card_layout = QVBoxLayout(self.table_card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("ec2_targets")
        self.table.setHorizontalHeaderLabels(
            ["★", "이름", "Instance ID", "Private IP", "EC2 상태", ""]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.verticalHeader().setMinimumSectionSize(54)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(2, 148)
        self.table.setColumnWidth(3, 112)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 120)
        self.table.setShowGrid(False)
        use_first_column_selection_bar(self.table)
        card_layout.addWidget(self.table)
        layout.addWidget(self.table_card, 1)
        self.session_state = QLabel("외부 세션 없음")
        self.session_state.setObjectName("ec2_session_state")
        layout.addWidget(self.session_state)
        # Kept as a non-rendered compatibility action; visible actions live in each table row.
        self.open_button = QPushButton("터미널 열기")
        self.open_button.setObjectName("ec2_open")
        self.open_button.hide()
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self.refresh_session_states)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        """Compress fixed columns before the row action can be clipped."""

        compact = self.width() < 900
        self.table.setColumnWidth(0, 52 if compact else 60)
        self.table.setColumnWidth(2, 128 if compact else 148)
        self.table.setColumnWidth(3, 92 if compact else 112)
        self.table.setColumnWidth(4, 92 if compact else 100)
        self.table.setColumnWidth(5, 120)
        super().resizeEvent(event)

    def set_profile(self, profile_id: int | None, region: str | None = None) -> None:
        selected_region = region or self._region
        if self._profile_id == profile_id and self._region == selected_region:
            return
        self._profile_id = profile_id
        self._region = selected_region
        self.region.setText(selected_region)
        self._targets = []
        self._render_targets()
        if profile_id is not None:
            self.reload()

    def reload(self) -> None:
        if self._profile_id is None:
            return
        profile_id = self._profile_id
        region = self.region.text().strip()
        self.refresh_button.setEnabled(False)

        def action() -> list[Ec2Target]:
            return self._service.list_targets_in_region(profile_id, region)

        if self._authenticated is None:
            self._runner.submit(action, self._targets_loaded, self._failed)
        else:
            self._authenticated.submit(
                profile_id, action, self._targets_loaded, self._failed, self._cancelled
            )

    def _targets_loaded(self, value: Any) -> None:
        self._action_busy = False
        self.refresh_button.setEnabled(True)
        self._targets = list(value)
        self._render_targets()

    def _render_targets(self) -> None:
        visible = filter_ec2_targets(
            self._targets,
            Ec2TargetFilter(
                keyword=self.filter.text(),
                ping_status="all",
                instance_state=str(self.status.currentData()),
            ),
        )
        self._target_by_id = {target.instance_id: target for target in visible}
        self.table.setRowCount(len(visible))
        for row, target in enumerate(visible):
            values = {
                0: "",
                1: target.name or "—",
                2: target.instance_id,
                3: target.private_ip_address or "—",
            }
            for column, value in values.items():
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, target.instance_id)
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
            favorite = QPushButton()
            favorite.setObjectName("ec2_favorite")
            favorite.setProperty("icon_only", True)
            favorite.setIcon(
                gui_icon("star-solid-full.svg" if target.favorite else "star-regular-full.svg")
            )
            favorite.setIconSize(QSize(18, 18))
            favorite.setFixedSize(30, 30)
            favorite_action = "해제" if target.favorite else "추가"
            favorite.setAccessibleName(
                f"{target.name or target.instance_id} 즐겨찾기 {favorite_action}"
            )
            favorite.clicked.connect(
                lambda _checked=False, value=target: self.toggle_favorite(value)
            )
            self.table.setCellWidget(row, 0, favorite)
            action = QPushButton(_target_action_text(target))
            action.setObjectName("ec2_row_action")
            action.setProperty("size", "small")
            action.setProperty("role", _target_action_role(target))
            action.setEnabled(_target_action_enabled(target) and not self._action_busy)
            action.setFixedWidth(112)
            action.clicked.connect(
                lambda _checked=False, value=target: self.run_target_action(value)
            )
            self.table.setCellWidget(row, 5, action)
            self.table.setRowHeight(row, 54)
        self._sync_actions()

    def _sync_actions(self) -> None:
        self.open_button.setEnabled(
            self.table.currentRow() >= 0 and self._profile_id is not None and not self._action_busy
        )

    def run_target_action(self, target: Ec2Target) -> None:
        if self._action_busy or self._profile_id is None:
            return
        self._select_target(target.instance_id)
        if target.instance_state == "stopped":
            self._run_power_action(target, "start")
        elif target.instance_state == "running" and target.ping_status != "Online":
            self._run_power_action(target, "reboot")
        else:
            self.open_selected()

    def toggle_favorite(self, target: Ec2Target) -> None:
        if self._action_busy or self._profile_id is None:
            return
        profile_id = self._profile_id
        region = self.region.text().strip()
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
        region = self.region.text().strip()
        self._action_busy = True
        self._render_targets()
        self.session_state.setText(
            "인스턴스 시작 요청 중…" if action == "start" else "재부팅 요청 중…"
        )
        operation = (
            self._service.start_instance if action == "start" else self._service.reboot_instance
        )
        self._runner.submit(
            lambda: operation(target.instance_id, profile_id, region=region),
            lambda _value: self._power_requested(action),
            self._failed,
        )

    def _power_requested(self, action: str) -> None:
        self._action_busy = False
        self.session_state.setText(
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
        row = self.table.currentRow()
        if row < 0 or self._profile_id is None:
            return
        selected = self.table.item(row, 0)
        if selected is None:
            return
        instance_id = str(selected.data(Qt.ItemDataRole.UserRole))
        profile_id = self._profile_id
        region = self.region.text().strip()
        self.open_button.setEnabled(False)
        self._action_busy = True
        self._render_targets()
        self.session_state.setText("외부 터미널 시작 중…")

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
        handle = value
        self.session_state.setText(
            f"{handle.instance_id} · PID {handle.process_id} · {handle.state.value}"
        )
        self.notice_raised.emit("EC2 세션을 외부 Windows Terminal에서 열었습니다.")
        self._action_busy = False
        self._render_targets()
        self.open_button.setEnabled(True)
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
        running = [handle for handle in handles if handle.state is OperationState.RUNNING]
        if running:
            self.session_state.setText(
                " · ".join(f"{item.instance_id} (PID {item.process_id})" for item in running)
            )
        elif handles:
            latest = handles[-1]
            self.session_state.setText(f"{latest.instance_id} · {latest.state.value}")
            if latest.error is not None:
                self.error_raised.emit(latest.error)
        else:
            self.session_state.setText("외부 세션 없음")
        if not any_running:
            self._poll_timer.stop()

    def _failed(self, error: ApplicationError) -> None:
        self._action_busy = False
        self._render_targets()
        self.refresh_button.setEnabled(self._profile_id is not None)
        self.open_button.setEnabled(self.table.currentRow() >= 0)
        self.session_state.setText("작업 실패 · 다시 시도할 수 있습니다.")
        self.error_raised.emit(error)

    def _cancelled(self) -> None:
        self.refresh_button.setEnabled(self._profile_id is not None)
        self.open_button.setEnabled(self.table.currentRow() >= 0)
        self.session_state.setText("MFA 인증 취소")


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
        self._endpoints = endpoints
        self._profile_id: int | None = None
        self._saved: dict[int, TunnelSession] = {}
        self._active: dict[str, TunnelConnectionResult] = {}
        self._selected_id: int | None = None
        self._relays_ready = False
        self._connection_busy = False
        self._editor_baseline: tuple[object, ...] | None = None
        self._build_ui()
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
        subtitle = QLabel("PuTTY 세션처럼 연결 정보를 저장하고 다시 사용할 수 있습니다.")
        subtitle.setObjectName("page_subtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        heading.addLayout(title_block)
        heading.addStretch()
        self.new_button = QPushButton("＋ 새 세션")
        self.new_button.setObjectName("rds_new")
        self.new_button.clicked.connect(lambda: self.new_session())
        heading.addWidget(self.new_button)
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
        self.search.setPlaceholderText("세션명 또는 RDS Host")
        self.search.textChanged.connect(self._render_sessions)
        self.session_list = QListWidget()
        self.session_list.setObjectName("rds_sessions")
        self.session_list.currentItemChanged.connect(self._select_session)
        left.addWidget(_field("저장 세션 검색", self.search))
        left.addWidget(self.session_list, 1)
        content.addWidget(self.session_card)
        self.editor_card = QFrame()
        self.editor_card.setObjectName("editor_card")
        editor = QVBoxLayout(self.editor_card)
        self.editor_layout = editor
        editor.setContentsMargins(20, 20, 20, 20)
        editor.setSpacing(0)
        editor_heading = QHBoxLayout()
        self.editor_status_dot = QFrame()
        self.editor_status_dot.setObjectName("status_dot")
        self.editor_title = QLabel("새 세션")
        self.editor_title.setObjectName("rds_editor_title")
        self.editor_subtitle = QLabel("연결 정보를 입력하고 저장하세요.")
        self.editor_subtitle.setObjectName("page_subtitle")
        editor_title_block = QVBoxLayout()
        editor_title_block.setSpacing(4)
        editor_title_block.addWidget(self.editor_title)
        editor_title_block.addWidget(self.editor_subtitle)
        editor_heading.addWidget(self.editor_status_dot)
        editor_heading.addLayout(editor_title_block)
        editor_heading.addStretch()
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
        self.remote_port.setRange(1, 65535)
        self.remote_port.setValue(3306)
        self.local_port = QSpinBox()
        self.local_port.setRange(1, 65535)
        self.local_port.setValue(13306)
        self.relay = QComboBox()
        self.relay.setObjectName("rds_relay")
        self.relay.currentIndexChanged.connect(self._sync_editor_actions)
        self.name_field = _field("세션명", self.name)
        form.addWidget(self.name_field, 0, 0, 1, 2)
        form.addWidget(_field("중계 EC2", self.relay), 1, 0, 1, 2)
        host_controls = QWidget()
        host_controls.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        host_layout = QVBoxLayout(host_controls)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(6)
        host_layout.addWidget(self.host_catalog)
        host_layout.addWidget(self.host)
        form.addWidget(_field("RDS Host", host_controls), 2, 0, 1, 2)
        form.addWidget(_field("Remote Port", self.remote_port), 3, 0)
        form.addWidget(_field("Local Port", self.local_port), 3, 1)
        details.addLayout(form)
        self.relay_status = QLabel("중계 EC2 목록을 불러오기 전에는 저장할 수 없습니다.")
        self.relay_status.setObjectName("rds_relay_status")
        self.relay_status.setWordWrap(True)
        details.addWidget(self.relay_status)
        details.addSpacing(16)
        self.tunnel_state = QLabel("중지됨")
        self.tunnel_state.setObjectName("rds_tunnel_state")
        self.tunnel_state.setWordWrap(False)
        self.copy_address_button = QPushButton("로컬 주소 복사")
        self.copy_address_button.setObjectName("rds_copy_address")
        self.copy_address_button.setAccessibleName("실행 중인 RDS 터널 로컬 주소 복사")
        self.copy_address_button.setEnabled(False)
        self.copy_address_button.clicked.connect(self.copy_selected_address)
        self.notice = QFrame()
        self.notice.setObjectName("notice")
        self.notice.setFixedHeight(42)
        notice_layout = QVBoxLayout(self.notice)
        notice_layout.setContentsMargins(12, 6, 12, 6)
        notice_layout.addWidget(self.tunnel_state)
        details.addWidget(self.notice)
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
        self.delete_button = QPushButton("삭제")
        self.delete_button.setProperty("role", "danger")
        self.clone_button = QPushButton("복제")
        self.save_button = QPushButton("저장")
        self.connection_button = QPushButton("연결 시작")
        self.connection_button.setObjectName("rds_connection")
        # Transitional aliases keep callers source-compatible while one actual button is rendered.
        self.start_button = self.connection_button
        self.stop_button = self.connection_button
        for button in (
            self.copy_address_button,
            self.clone_button,
            self.delete_button,
            self.save_button,
            self.connection_button,
        ):
            button.setProperty("size", "small")
        self.delete_button.clicked.connect(self.delete_selected)
        self.clone_button.clicked.connect(self.clone_selected)
        self.save_button.clicked.connect(self.save)
        self.connection_button.clicked.connect(self.toggle_connection)
        self.editor_actions = actions
        self._layout_editor_actions()
        details.addWidget(self.editor_actions_container)
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
        self.new_session(confirm_discard=False)

    def set_profile(self, profile_id: int | None) -> None:
        if self._profile_id == profile_id:
            return
        self._profile_id = profile_id
        self._relays_ready = False
        self.relay.clear()
        self.relay_status.setText("중계 EC2 목록을 불러오는 중입니다…")
        self.new_session(confirm_discard=False)
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
            for session in self._saved.values():
                if query and query not in f"{session.name} {session.host}".casefold():
                    continue
                is_active = any(active.tunnel.id == session.id for active in self._active.values())
                item = QListWidgetItem(
                    f"{session.name}\nlocalhost:{session.local_port} · "
                    f"{'연결됨' if is_active else '중지됨'}"
                )
                item.setData(Qt.ItemDataRole.UserRole, session.require_id())
                self.session_list.addItem(item)
                if session.id == self._selected_id:
                    self.session_list.setCurrentItem(item)

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

    def new_session(self, *, confirm_discard: bool = True) -> None:
        if confirm_discard and self._is_editor_dirty() and not self._discard_confirmation(self):
            return
        self._selected_id = None
        self.name_field.show()
        self.session_list.clearSelection()
        self.editor_title.setText("새 세션")
        self.editor_subtitle.setText("연결 정보를 입력하고 저장하세요.")
        self.name.clear()
        self.host.clear()
        self.remote_port.setValue(3306)
        self.local_port.setValue(13306)
        self._editor_baseline = self._editor_values()
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
        session_id = int(current.data(Qt.ItemDataRole.UserRole))
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
        self._sync_editor_actions()

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
        self.new_session(confirm_discard=False)
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

    def _saved_done(self, _value: Any) -> None:
        self.notice_raised.emit("RDS 터널 세션을 저장했습니다.")
        self._editor_baseline = self._editor_values()
        self.reload()

    def delete_selected(self) -> None:
        if self._selected_id is None or self._profile_id is None:
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
        self.new_session(confirm_discard=False)
        self.reload()

    def start(self, tunnel_id: int | None = None) -> None:
        if (
            self._connection_busy
            or (tunnel_id is None and self._selected_id is None)
            or self._profile_id is None
            or not self._relay_available()
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
        self._sync_editor_actions()

    def _active_selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            self.copy_address_button.setEnabled(False)
            return
        operation_id = str(current.data(Qt.ItemDataRole.UserRole))
        active = self._active.get(operation_id)
        if active is None:
            self.copy_address_button.setEnabled(False)
            return
        address = f"127.0.0.1:{active.tunnel.local_port}"
        self.copy_address_button.setProperty("local_address", address)
        self.copy_address_button.setEnabled(True)
        self.tunnel_state.setText(
            f"연결됨 · {address} → {active.tunnel.host}:{active.tunnel.remote_port}"
        )

    def copy_selected_address(self) -> None:
        address = self.copy_address_button.property("local_address")
        if isinstance(address, str) and address:
            copy_temporarily(address)
            self._address_copied(address)

    def _address_copied(self, _address: str) -> None:
        self.notice_raised.emit("로컬 주소를 복사했습니다. 30초 후 클립보드에서 제거합니다.")

    def stop_selected(self) -> None:
        operation_id = self._selected_operation_id()
        if operation_id is None:
            return
        self.stop_operation(operation_id)

    def toggle_connection(self) -> None:
        """Run the sole connection action for the selected saved session."""

        operation_id = self._selected_operation_id()
        if operation_id is None:
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
        self.delete_button.setEnabled(selected and not self._connection_busy)
        self.clone_button.setEnabled(selected and not self._connection_busy)
        self.save_button.setEnabled(
            self._profile_id is not None and relay_available and not self._connection_busy
        )
        self.connection_button.setText("연결 종료" if operation_id is not None else "연결 시작")
        self.connection_button.setProperty(
            "role", "danger" if operation_id is not None else "primary"
        )
        self.connection_button.style().unpolish(self.connection_button)
        self.connection_button.style().polish(self.connection_button)
        self.connection_button.setEnabled(
            selected and relay_available and not self._connection_busy
        )
        self.editor_status_dot.setProperty("active", operation_id is not None)
        self.editor_status_dot.setVisible(operation_id is not None)
        if selected:
            self.editor_subtitle.setText(
                "현재 터널이 실행 중입니다."
                if operation_id is not None
                else "저장된 터널 세션 · 중지됨"
            )

    def _layout_editor_actions(self) -> None:
        for button in (
            self.copy_address_button,
            self.clone_button,
            self.delete_button,
            self.save_button,
            self.connection_button,
        ):
            self.editor_actions.removeWidget(button)
        for column in range(6):
            self.editor_actions.setColumnStretch(column, 0)
        self.editor_actions.addWidget(self.copy_address_button, 0, 0)
        self.editor_actions.addWidget(self.clone_button, 0, 1)
        self.editor_actions.addWidget(self.delete_button, 0, 2)
        self.editor_actions.setColumnStretch(3, 1)
        self.editor_actions.addWidget(self.save_button, 0, 4)
        self.editor_actions.addWidget(self.connection_button, 0, 5)

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


def _field(label: str, control: QWidget) -> QWidget:
    container = QWidget()
    container.setObjectName("field")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    caption = QLabel(label)
    caption.setObjectName("field_label")
    layout.addWidget(caption)
    layout.addWidget(control)
    return container


def _status_label(text: str, status: str) -> QLabel:
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
    if target.instance_state == "running" and target.ping_status != "Online":
        return "재부팅"
    return "터미널 열기 ↗"


def _target_action_role(target: Ec2Target) -> str:
    if target.instance_state == "running" and target.ping_status != "Online":
        return "danger"
    if target.ping_status == "Online":
        return "primary"
    return "default"


def _target_action_enabled(target: Ec2Target) -> bool:
    if not target.power_actions_available and target.ping_status != "Online":
        return False
    return target.instance_state in {None, "running", "stopped"}
