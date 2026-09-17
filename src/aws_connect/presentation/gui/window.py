"""PySide6 shell for profiles, authentication, MFA, and the dashboard."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from aws_connect import APPLICATION_NAME
from aws_connect.application.activity_log_service import ActivityLogService
from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.authentication_service import (
    AuthenticationService,
    AuthenticationStatus,
    OperationCoordinator,
)
from aws_connect.application.connection_lifecycle import (
    ProfileConnectionLifecycleService,
    ProfileConnections,
)
from aws_connect.application.ec2_service import Ec2Service
from aws_connect.application.operations import OperationResult, OperationState
from aws_connect.application.profile_service import (
    ProfileService,
    ProfileSummary,
    SaveProfileRequest,
)
from aws_connect.application.rds_endpoint_service import RdsEndpointService
from aws_connect.application.rds_tunnel_operation import RdsTunnelOperationCoordinator
from aws_connect.application.rds_tunnel_service import TunnelSessionService
from aws_connect.application.s3_service import S3LocationService, S3Service
from aws_connect.application.secrets_service import SecretsService
from aws_connect.application.settings_service import DiagnosticLogService, SettingsService
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.presentation.gui.ec2_rds import ActiveTunnelRow, Ec2Page, RdsPage
from aws_connect.presentation.gui.errors import (
    GuiErrorPresentation,
    GuiErrorViewModel,
    map_error,
)
from aws_connect.presentation.gui.icons import (
    HEADER_ICON_SIZE,
    NAVIGATION_ICON_SIZE,
    gui_asset_path,
    gui_icon,
    set_button_icon,
)
from aws_connect.presentation.gui.list_rows import (
    set_compact_list_row,
    update_list_row_separators,
)
from aws_connect.presentation.gui.logs import LogsSettingsPage
from aws_connect.presentation.gui.s3 import S3Page
from aws_connect.presentation.gui.secrets import SecretsPage
from aws_connect.presentation.gui.styles import APP_STYLE, NAVIGATION_WIDTH
from aws_connect.presentation.gui.tasks import GuiTaskRunner
from aws_connect.presentation.gui.view_models import (
    ActiveTunnelSummaryViewModel,
    AuthenticationHeaderViewModel,
    build_authentication_header,
    checking_authentication_header,
    empty_authentication_header,
)

MfaCodeProvider = Callable[[QWidget, str], str | None]
DeleteConfirmation = Callable[[QWidget, int], bool]
ActiveDeleteConfirmation = Callable[[QWidget, ProfileConnections], bool]
ProfileCloneNameProvider = Callable[[QWidget, str], tuple[str, bool]]


class MfaDialog(QDialog):
    """Small credential-safe modal used only for the six-digit MFA code."""

    def __init__(self, parent: QWidget, device_arn: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("MFA 인증")
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"MFA 장치: {device_arn}"))
        layout.addWidget(QLabel("인증 앱에 표시된 6자리 코드를 입력하세요."))
        self.code = QLineEdit()
        self.code.setObjectName("mfa_code")
        self.code.setInputMask("000000")
        self.code.setEchoMode(QLineEdit.EchoMode.Password)
        self.code.setAccessibleName("MFA 코드")
        layout.addWidget(self.code)
        actions = QHBoxLayout()
        cancel = QPushButton("취소")
        confirm = QPushButton("인증")
        confirm.setDefault(True)
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        layout.addLayout(actions)

    @classmethod
    def request(cls, parent: QWidget, device_arn: str) -> str | None:
        dialog = cls(parent, device_arn)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.code.text()


class ProfileDialog(QDialog):
    """Profile editor; secret values are never populated back into fields."""

    save_requested = Signal(object)
    delete_requested = Signal(int)
    connect_requested = Signal(int)
    clone_requested = Signal(int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("profile_dialog")
        self.setWindowTitle("AWS 프로필 관리")
        self.setModal(True)
        self.setMaximumWidth(920)
        self.setMaximumHeight(720)
        self.setMinimumHeight(556)
        self.resize(920, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        dialog_header = QFrame()
        dialog_header.setObjectName("profile_dialog_header")
        dialog_header.setFixedHeight(76)
        header_layout = QHBoxLayout(dialog_header)
        header_layout.setContentsMargins(20, 14, 20, 14)
        header_copy = QVBoxLayout()
        header_copy.setSpacing(3)
        dialog_title = QLabel("AWS 프로필 관리")
        dialog_title.setObjectName("section_title")
        dialog_subtitle = QLabel("저장된 인증정보를 관리하거나 다른 프로필로 연결합니다.")
        dialog_subtitle.setObjectName("page_subtitle")
        header_copy.addWidget(dialog_title)
        header_copy.addWidget(dialog_subtitle)
        header_layout.addLayout(header_copy)
        header_layout.addStretch()
        root.addWidget(dialog_header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        profile_panel = QFrame()
        profile_panel.setObjectName("profile_list_panel")
        left = QVBoxLayout(profile_panel)
        left.setContentsMargins(14, 14, 14, 14)
        left.setSpacing(4)
        self.profile_list = QListWidget()
        self.profile_list.setObjectName("profile_list")
        self.new_button = QPushButton("새 프로필")
        self.new_button.setObjectName("profile_new_button")
        self.new_button.setProperty("variant", "primary")
        self.new_button.clicked.connect(self.new_profile)
        left.addWidget(self.new_button)
        left.addWidget(self.profile_list, 1)
        body.addWidget(profile_panel)

        editor_panel = QWidget()
        editor_panel.setObjectName("profile_editor")
        editor = QVBoxLayout(editor_panel)
        editor.setContentsMargins(22, 22, 22, 22)
        editor.setSpacing(5)
        self.name = QLineEdit()
        self.name.setObjectName("profile_name_input")
        self.account = QLineEdit()
        self.account.setObjectName("profile_account_input")
        self.user = QLineEdit()
        self.user.setObjectName("profile_user_input")
        self.region = QLineEdit("ap-northeast-2", editor_panel)
        self.region.setObjectName("profile_region_input")
        self.region.textChanged.connect(lambda _value: self._clear_field_error(self.region))
        self.region.hide()
        self.access_key = QLineEdit()
        self.access_key.setObjectName("profile_access_key_input")
        self.secret_key = QLineEdit()
        self.secret_key.setObjectName("profile_secret_key_input")
        self.access_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._show_new_credential_placeholders()
        self.access_key.setAccessibleName("Access Key ID")
        self.secret_key.setAccessibleName("Secret Access Key")
        self.mfa_enabled = QCheckBox("토큰 발급 시 2차 인증 사용")
        self.mfa_enabled.setObjectName("profile_mfa_enabled")
        self.mfa_enabled.setChecked(True)
        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(14)

        def add_field(label: str, field: QLineEdit, row: int, column: int, span: int = 1) -> None:
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(0, 0, 0, 0)
            block_layout.setSpacing(6)
            caption = QLabel(label)
            caption.setObjectName("field_label")
            block_layout.addWidget(caption)
            block_layout.addWidget(field)
            form.addWidget(block, row, column, 1, span)
            field.textChanged.connect(
                lambda _value, selected=field: self._clear_field_error(selected)
            )

        add_field("프로필명", self.name, 0, 0, 2)
        add_field("Account ID", self.account, 1, 0)
        add_field("IAM 사용자", self.user, 1, 1)
        add_field("Access Key ID", self.access_key, 2, 0, 2)
        add_field("Secret Access Key", self.secret_key, 3, 0, 2)
        editor.addLayout(form)
        editor.addWidget(self.mfa_enabled)
        notice = QLabel(
            "Secret Access Key와 발급 토큰은 OS 사용자 범위로 암호화하여 SQLite에 저장합니다."
        )
        notice.setObjectName("profile_notice")
        notice.setWordWrap(True)
        editor.addWidget(notice)
        editor.addStretch()
        actions = QHBoxLayout()
        self.delete_button = QPushButton()
        self.delete_button.setProperty("variant", "danger")
        self.clone_button = QPushButton()
        self.save_button = QPushButton()
        self.connect_button = QPushButton()
        self.connect_button.setProperty("variant", "primary")
        for button, icon, label, color in (
            (self.delete_button, "common-delete.svg", "삭제", "#ffffff"),
            (self.clone_button, "common-copy.svg", "복제", None),
            (self.save_button, "common-save.svg", "저장", None),
            (self.connect_button, "common-start.svg", "이 프로필로 연결", "#ffffff"),
        ):
            button.setProperty("action_button", True)
            set_button_icon(button, icon, tooltip=label, color=color)
        self.delete_button.clicked.connect(self._request_delete)
        self.clone_button.clicked.connect(self._request_clone)
        self.save_button.clicked.connect(self._request_save)
        self.connect_button.clicked.connect(self._request_connect)
        list_actions = QHBoxLayout()
        list_actions.addWidget(self.delete_button)
        list_actions.addStretch()
        list_actions.addWidget(self.clone_button)
        left.addLayout(list_actions)
        actions.addStretch()
        actions.addWidget(self.save_button)
        actions.addWidget(self.connect_button)
        editor.addLayout(actions)
        body.addWidget(editor_panel, 1)
        root.addLayout(body, 1)
        self.profile_list.currentItemChanged.connect(self._select_item)
        self._profiles: dict[int, ProfileSummary] = {}
        self._selected_id: int | None = None
        self.new_profile()

    def set_profiles(self, profiles: list[ProfileSummary], selected_id: int | None = None) -> None:
        self._profiles = {profile.id: profile for profile in profiles}
        self.profile_list.clear()
        for profile in profiles:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.profile_list.addItem(item)
            set_compact_list_row(
                self.profile_list,
                item,
                profile.name + (" · 현재 연결" if profile.is_default else ""),
                (f"Account ID  {profile.account_id}", f"IAM  {profile.user_id}"),
                connected=profile.is_default,
                status_alignment=Qt.AlignmentFlag.AlignTop,
            )
            if profile.id == selected_id:
                self.profile_list.setCurrentItem(item)
        if self.profile_list.currentItem() is None and self.profile_list.count():
            self.profile_list.setCurrentRow(0)
        update_list_row_separators(self.profile_list)

    def new_profile(self) -> None:
        draft = next(
            (
                self.profile_list.item(index)
                for index in range(self.profile_list.count())
                if self.profile_list.item(index).data(Qt.ItemDataRole.UserRole) is None
            ),
            None,
        )
        if draft is None:
            draft = QListWidgetItem()
            draft.setData(Qt.ItemDataRole.UserRole, None)
            self.profile_list.insertItem(0, draft)
            set_compact_list_row(self.profile_list, draft, "새 프로필", ("저장되지 않음",))
            update_list_row_separators(self.profile_list)
        self.profile_list.setCurrentItem(draft)
        self._show_new_editor()

    def _show_new_editor(self) -> None:
        self._selected_id = None
        for field in (
            self.name,
            self.account,
            self.user,
            self.access_key,
            self.secret_key,
        ):
            field.clear()
        self._show_new_credential_placeholders()
        self.region.setText("ap-northeast-2")
        self.mfa_enabled.setChecked(True)
        self.delete_button.setEnabled(True)
        self.clone_button.setEnabled(False)
        self.connect_button.setEnabled(False)

    def _select_item(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        selected_data = current.data(Qt.ItemDataRole.UserRole)
        if selected_data is None:
            self._show_new_editor()
            return
        profile_id = int(selected_data)
        profile = self._profiles[profile_id]
        self._selected_id = profile_id
        self.name.setText(profile.name)
        self.account.setText(profile.account_id)
        self.user.setText(profile.user_id)
        self.region.setText(profile.region)
        self.mfa_enabled.setChecked(profile.mfa_enabled)
        self.access_key.clear()
        self.secret_key.clear()
        self._show_saved_credential_placeholders()
        self.delete_button.setEnabled(True)
        self.clone_button.setEnabled(True)
        self.connect_button.setEnabled(True)

    def _request_save(self) -> None:
        access_key = self.access_key.text() or None
        secret_key = self.secret_key.text() or None
        self.save_requested.emit(
            SaveProfileRequest(
                name=self.name.text().strip(),
                region=self.region.text().strip(),
                account_id=self.account.text().strip(),
                user_id=self.user.text().strip(),
                access_key=access_key,
                secret_key=secret_key,
                # The implementation-level MFA ARN is derived for new profiles and
                # preserved by ProfileService for edits.
                mfa_arn=None,
                mfa_enabled=self.mfa_enabled.isChecked(),
                profile_id=self._selected_id,
            )
        )

    def _show_new_credential_placeholders(self) -> None:
        self.access_key.setPlaceholderText("Access Key ID 입력")
        self.secret_key.setPlaceholderText("Secret Access Key 입력")

    def _show_saved_credential_placeholders(self) -> None:
        masked = "•••••••••••••••• · 등록됨 (변경 시에만 입력)"
        self.access_key.setPlaceholderText(masked)
        self.secret_key.setPlaceholderText(masked)

    def _request_delete(self) -> None:
        if self._selected_id is not None:
            self.delete_requested.emit(self._selected_id)
            return
        draft = self.profile_list.currentItem()
        if draft is None or draft.data(Qt.ItemDataRole.UserRole) is not None:
            return
        self.profile_list.takeItem(self.profile_list.row(draft))
        update_list_row_separators(self.profile_list)
        if self.profile_list.count():
            self.profile_list.setCurrentRow(0)
        else:
            self.new_profile()

    def _request_connect(self) -> None:
        if self._selected_id is not None:
            self.connect_requested.emit(self._selected_id)

    def _request_clone(self) -> None:
        if self._selected_id is not None:
            self.clone_requested.emit(self._selected_id)

    @staticmethod
    def _clear_field_error(field: QLineEdit) -> None:
        if not field.property("application_error"):
            return
        field.setProperty("application_error", False)
        field.setToolTip("")
        field.style().unpolish(field)
        field.style().polish(field)


class FeatureCard(QFrame):
    activated = Signal(str)

    def __init__(
        self,
        route: str,
        icon_filename: str,
        title: str,
        subtitle: str,
        description: str,
        action: str,
    ) -> None:
        super().__init__()
        self.setObjectName("feature_card")
        self.setProperty("feature", route)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(13)
        icon = QLabel()
        icon.setObjectName("feature_icon")
        icon.setPixmap(gui_icon(icon_filename).pixmap(24, 24))
        top.addWidget(icon)
        heading_copy = QVBoxLayout()
        heading_copy.setSpacing(3)
        heading = QLabel(title)
        heading.setObjectName("feature_heading")
        heading_copy.addWidget(heading)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("feature_subtitle")
        heading_copy.addWidget(subtitle_label)
        top.addLayout(heading_copy)
        top.addStretch()
        button = QPushButton()
        button.setObjectName(f"dashboard_{route}_button")
        button.setProperty("variant", "secondary")
        button.setProperty("icon_only", True)
        set_button_icon(button, "dashboard-move.svg", tooltip=action)
        button.clicked.connect(lambda: self.activated.emit(route))
        top.addWidget(button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        description_label = QLabel(description)
        description_label.setObjectName("feature_description")
        description_label.setWordWrap(True)
        description_label.setContentsMargins(0, 14, 0, 18)
        layout.addWidget(description_label)
        layout.addStretch()


class MainWindow(QMainWindow):
    """The Phase 4 application shell backed only by Application Services."""

    def __init__(
        self,
        profiles: ProfileService,
        authentication: AuthenticationService,
        operations: OperationCoordinator,
        ec2: Ec2Service | None = None,
        tunnel_sessions: TunnelSessionService | None = None,
        rds_tunnels: RdsTunnelOperationCoordinator | None = None,
        secrets: SecretsService | None = None,
        s3_locations: S3LocationService | None = None,
        s3: S3Service | None = None,
        settings: SettingsService | None = None,
        activity_logs: ActivityLogService | None = None,
        diagnostic_logs: DiagnosticLogService | None = None,
        rds_endpoints: RdsEndpointService | None = None,
        *,
        connection_lifecycle: ProfileConnectionLifecycleService,
        authenticated_operations: AuthenticatedOperationCoordinator | None = None,
        task_runner: GuiTaskRunner | None = None,
        mfa_code_provider: MfaCodeProvider | None = None,
        delete_confirmation: DeleteConfirmation | None = None,
        active_delete_confirmation: ActiveDeleteConfirmation | None = None,
        profile_clone_name_provider: ProfileCloneNameProvider | None = None,
        auto_start: bool = True,
    ) -> None:
        super().__init__()
        self._profiles = profiles
        self._authentication = authentication
        self._operations = operations
        self._ec2 = ec2
        self._tunnel_sessions = tunnel_sessions
        self._rds_tunnels = rds_tunnels
        self._secrets = secrets
        self._s3_locations = s3_locations
        self._s3 = s3
        self._settings = settings
        self._activity_logs = activity_logs
        self._diagnostic_logs = diagnostic_logs
        self._rds_endpoints = rds_endpoints
        self._authenticated_operations = authenticated_operations
        self._runner = task_runner or GuiTaskRunner()
        self._mfa_code_provider = mfa_code_provider or MfaDialog.request
        self._delete_confirmation = delete_confirmation or _confirm_profile_delete
        self._active_delete_confirmation = (
            active_delete_confirmation or _confirm_active_profile_delete
        )
        self._profile_clone_name_provider = profile_clone_name_provider or _ask_profile_clone_name
        self._connection_lifecycle = connection_lifecycle
        self._profile_summaries: list[ProfileSummary] = []
        self._active_profile_id: int | None = None
        self.feature_data_revision = 0
        self.last_error: GuiErrorViewModel | None = None
        self.error_dialog: QMessageBox | None = None
        self.activity_log_error_code: str | None = None
        self._closing = False
        self._allow_close = False
        self.setWindowTitle(APPLICATION_NAME)
        self.setMinimumSize(1024, 720)
        self.resize(1424, 894)
        self._build_ui()
        self._apply_header(empty_authentication_header())
        if auto_start:
            QTimer.singleShot(0, self.reload_profiles)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("prototype_background")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        app_shell = QFrame()
        app_shell.setObjectName("app_shell")
        app_layout = QVBoxLayout(app_shell)
        app_layout.setContentsMargins(0, 0, 0, 0)
        app_layout.setSpacing(0)
        app_layout.addWidget(self._build_header())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.pages = QStackedWidget()
        self.pages.setObjectName("workspace")
        body.addWidget(self._build_navigation())
        self.pages.addWidget(self._build_dashboard())
        self.ec2_page: Ec2Page | None = None
        self.rds_page: RdsPage | None = None
        self.secrets_page: SecretsPage | None = None
        self.s3_page: S3Page | None = None
        self.logs_page: LogsSettingsPage | None = None
        if self._ec2 is not None:
            self.ec2_page = Ec2Page(
                self._ec2,
                self._runner,
                self._authenticated_operations,
                self._mfa_code_provider,
            )
            self.ec2_page.error_raised.connect(self._show_error)
            self.ec2_page.notice_raised.connect(self._notify)
            self.pages.addWidget(self.ec2_page)
        else:
            self.pages.addWidget(self._placeholder("EC2 접속"))
        if (
            self._ec2 is not None
            and self._tunnel_sessions is not None
            and self._rds_tunnels is not None
        ):
            self.rds_page = RdsPage(
                self._tunnel_sessions,
                self._rds_tunnels,
                self._ec2,
                self._runner,
                self._mfa_code_provider,
                _confirm_tunnel_delete,
                authenticated=self._authenticated_operations,
                endpoints=self._rds_endpoints,
            )
            self.rds_page.error_raised.connect(self._show_error)
            self.rds_page.notice_raised.connect(self._notify)
            self.rds_page.tunnels_changed.connect(self.update_active_tunnels)
            self.pages.addWidget(self.rds_page)
        else:
            self.pages.addWidget(self._placeholder("RDS 터널"))
        if self._secrets is not None:
            self.secrets_page = SecretsPage(
                self._secrets,
                self._runner,
                self._authenticated_operations,
                self._mfa_code_provider,
                ec2=self._ec2,
            )
            self.secrets_page.error_raised.connect(self._show_error)
            self.secrets_page.notice_raised.connect(self._notify)
            self.secrets_page.rds_values_selected.connect(self._prefill_rds_endpoint)
            self.pages.addWidget(self.secrets_page)
        else:
            self.pages.addWidget(self._placeholder("Secrets Manager"))
        if self._s3_locations is not None and self._s3 is not None:
            self.s3_page = S3Page(
                self._s3_locations,
                self._s3,
                self._runner,
                authenticated=self._authenticated_operations,
                mfa_code_provider=self._mfa_code_provider,
            )
            self.s3_page.error_raised.connect(self._show_error)
            self.s3_page.notice_raised.connect(self._notify)
            self.pages.addWidget(self.s3_page)
        else:
            self.pages.addWidget(self._placeholder("S3 파일"))
        if (
            self._settings is not None
            and self._activity_logs is not None
            and self._diagnostic_logs is not None
        ):
            self.logs_page = LogsSettingsPage(
                self._settings, self._activity_logs, self._diagnostic_logs, self._runner
            )
            self.logs_page.error_raised.connect(self._show_error)
            self.logs_page.notice_raised.connect(self._notify)
            self.pages.addWidget(self.logs_page)
            QTimer.singleShot(0, self.logs_page.refresh)
        else:
            self.pages.addWidget(self._placeholder("실행 로그"))
        body.addWidget(self.pages, 1)
        app_layout.addLayout(body, 1)
        root_layout.addWidget(app_shell, 1)
        self.setCentralWidget(root)
        self.profile_dialog = ProfileDialog(self)
        self.profile_dialog.save_requested.connect(self.save_profile)
        self.profile_dialog.delete_requested.connect(self.delete_profile)
        self.profile_dialog.connect_requested.connect(self.connect_profile)
        self.profile_dialog.clone_requested.connect(self.clone_profile)
        self.toast = QLabel(root)
        self.toast.setObjectName("toast")
        self.toast.hide()
        self.setStyleSheet(APP_STYLE)

    @staticmethod
    def _placeholder(title: str) -> QWidget:
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        heading = QLabel(title)
        heading.setObjectName("page_title")
        layout.addWidget(heading)
        layout.addWidget(QLabel("이 기능은 다음 구현 단계에서 공통 서비스를 연결합니다."))
        layout.addStretch()
        return placeholder

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("authentication_header")
        header.setFixedHeight(92)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 24, 0)
        layout.setSpacing(0)

        brand_panel = QWidget()
        brand_panel.setObjectName("brand_panel")
        brand_panel.setFixedWidth(NAVIGATION_WIDTH)
        brand_layout = QHBoxLayout(brand_panel)
        brand_layout.setContentsMargins(15, 0, 15, 0)
        brand_layout.setSpacing(12)
        brand_logo = QLabel()
        brand_logo.setObjectName("brand_logo")
        logo = QPixmap(gui_asset_path("logo.png"))
        logo = logo.copy(68, 340, 1325, 353).scaled(
            190,
            52,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        brand_logo.setPixmap(logo)
        brand_logo.setFixedSize(190, 52)
        brand_logo.setAccessibleName(APPLICATION_NAME)
        brand_layout.addWidget(brand_logo)
        brand_layout.addStretch()
        layout.addWidget(brand_panel)

        summary = QWidget()
        summary.setObjectName("profile_summary")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(18, 12, 18, 12)
        summary_layout.setSpacing(16)
        self.profile_name = QLabel()
        self.profile_name.setObjectName("profile_name")
        self.auth_state = QLabel()
        self.auth_state.setObjectName("auth_state")
        self.account_id = QLabel()
        self.account_id.setObjectName("account_id")
        self.iam_user = QLabel()
        self.iam_user.setObjectName("iam_user")
        self.token_expiry = QLabel()
        self.token_expiry.setObjectName("token_expiry")
        metadata = QGridLayout()
        metadata.setHorizontalSpacing(22)
        metadata.setVerticalSpacing(3)
        for column, (label, value) in enumerate(
            (
                ("현재 프로필", self.profile_name),
                ("Account ID", self.account_id),
                ("IAM 사용자", self.iam_user),
                ("토큰 만료", self.token_expiry),
                ("인증 상태", self.auth_state),
            )
        ):
            caption = QLabel(label)
            caption.setObjectName("header_caption")
            metadata.addWidget(caption, 0, column)
            metadata.addWidget(value, 1, column)
        summary_layout.addLayout(metadata)
        summary_layout.addStretch()
        layout.addWidget(summary, 1)
        layout.addStretch()
        self.profile_button = QPushButton()
        self.profile_button.setObjectName("profile_button")
        set_button_icon(
            self.profile_button,
            "common-profile.svg",
            size=HEADER_ICON_SIZE,
            tooltip="프로필 관리",
        )
        self.profile_button.clicked.connect(self.open_profiles)
        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("refresh_button")
        set_button_icon(
            self.refresh_button,
            "common-refresh.svg",
            size=HEADER_ICON_SIZE,
            tooltip="토큰 재발급",
        )
        self.refresh_button.clicked.connect(self.refresh_token)
        layout.addSpacing(8)
        layout.addWidget(self.profile_button)
        layout.addSpacing(8)
        layout.addWidget(self.refresh_button)
        return header

    def _build_navigation(self) -> QWidget:
        navigation = QFrame()
        navigation.setObjectName("navigation")
        navigation.setFixedWidth(NAVIGATION_WIDTH)
        layout = QVBoxLayout(navigation)
        layout.setContentsMargins(14, 22, 14, 22)
        layout.setSpacing(6)
        label = QLabel("WORKSPACE")
        label.setObjectName("navigation_label")
        label.setContentsMargins(12, 0, 0, 3)
        layout.addWidget(label)
        group = QButtonGroup(navigation)
        group.setExclusive(True)
        entries = (
            ("tab-dashboard.svg", "대시보드"),
            ("tab-ec2.svg", "EC2 접속"),
            ("tab-rds.svg", "RDS 터널"),
            ("tab-secrets.svg", "Secrets"),
            ("tab-s3.svg", "S3 파일"),
            ("tab-log.svg", "실행 로그"),
        )
        for index, (icon_filename, title) in enumerate(entries):
            button = QPushButton(title)
            button.setObjectName(f"nav_{index}")
            button.setAccessibleName(title)
            set_button_icon(button, icon_filename, size=NAVIGATION_ICON_SIZE)
            button.setCheckable(True)
            button.setChecked(index == 0)
            group.addButton(button, index)
            button.clicked.connect(
                lambda _checked=False, page=index: self.pages.setCurrentIndex(page)
            )
            layout.addWidget(button)
        self.pages.currentChanged.connect(
            lambda index, buttons=group: (
                buttons.button(index).setChecked(True)
                if buttons.button(index) is not None
                else None
            )
        )
        layout.addStretch()
        return navigation

    def _build_dashboard(self) -> QWidget:
        dashboard = QWidget()
        dashboard.setObjectName("dashboard")
        layout = QVBoxLayout(dashboard)
        layout.setContentsMargins(34, 30, 34, 40)
        layout.setSpacing(0)
        heading = QLabel("연결 도구")
        heading.setObjectName("page_title")
        layout.addWidget(heading)
        subtitle = QLabel("현재 인증정보로 실행할 AWS 작업을 선택하세요.")
        subtitle.setObjectName("page_subtitle")
        subtitle.setContentsMargins(0, 3, 0, 24)
        layout.addWidget(subtitle)
        cards = QGridLayout()
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setHorizontalSpacing(16)
        cards.setVerticalSpacing(16)
        details = (
            (
                "ec2",
                "tab-ec2.svg",
                "EC2 접속",
                "외부 터미널",
                "온라인 인스턴스를 조회하고 선택한 서버에 Session Manager로 접속합니다.",
                "인스턴스 선택",
            ),
            (
                "rds",
                "tab-rds.svg",
                "RDS 터널",
                "앱 내부 세션 관리",
                "저장한 터널 세션을 선택해 로컬 포트포워딩을 시작하고 상태를 유지합니다.",
                "터널 관리",
            ),
            (
                "secrets",
                "tab-secrets.svg",
                "Secrets Manager",
                "민감정보 마스킹",
                "권한이 있는 Secret을 조회하고 JSON 값을 안전하게 확인하거나 복사합니다.",
                "시크릿 조회",
            ),
            (
                "s3",
                "tab-s3.svg",
                "S3 파일",
                "탐색 및 업로드",
                "Bucket과 Prefix를 탐색하고 로컬 파일을 선택하여 업로드합니다.",
                "S3 열기",
            ),
        )
        page_by_code = {"ec2": 1, "rds": 2, "secrets": 3, "s3": 4}
        for index, detail in enumerate(details):
            card = FeatureCard(*detail)
            card.activated.connect(
                lambda code, pages=page_by_code: self.pages.setCurrentIndex(pages[code])
            )
            cards.addWidget(card, index // 2, index % 2)
        cards.setColumnStretch(0, 1)
        cards.setColumnStretch(1, 1)
        layout.addLayout(cards)
        layout.addSpacing(18)
        tunnel = QFrame()
        tunnel.setObjectName("tunnel_summary")
        tunnel_layout = QVBoxLayout(tunnel)
        tunnel_layout.setContentsMargins(20, 14, 20, 14)
        title_row = QHBoxLayout()
        dot = QFrame()
        dot.setObjectName("status_dot")
        title_row.addWidget(dot)
        tunnel_heading = QLabel("활성 RDS 터널")
        tunnel_heading.setObjectName("active_tunnel_heading")
        title_row.addWidget(tunnel_heading)
        self.tunnel_summary = QLabel("실행 중인 터널이 없습니다.")
        self.tunnel_summary.setObjectName("active_tunnel_summary")
        title_row.addWidget(self.tunnel_summary)
        title_row.addStretch()
        tunnel_layout.addLayout(title_row)
        self.dashboard_tunnels = QListWidget()
        self.dashboard_tunnels.setObjectName("dashboard_active_tunnels")
        self.dashboard_tunnels.setMaximumHeight(134)
        self.dashboard_tunnels.setMinimumHeight(46)
        self.dashboard_tunnels.hide()
        tunnel_layout.addWidget(self.dashboard_tunnels)
        layout.addWidget(tunnel)
        layout.addStretch()
        return dashboard

    def reload_profiles(self, selected_id: int | None = None) -> None:
        self._runner.submit(
            self._profiles.list,
            lambda value: self._profiles_loaded(value, selected_id),
            self._show_error,
        )

    def _profiles_loaded(self, value: Any, selected_id: int | None) -> None:
        profiles = list(value)
        self._profile_summaries = profiles
        selected = next(
            (item for item in profiles if item.id == selected_id),
            next((item for item in profiles if item.is_default), None),
        )
        self.profile_dialog.set_profiles(profiles, selected.id if selected else None)
        if selected is None:
            self._active_profile_id = None
            self._apply_header(empty_authentication_header())
            self.refresh_button.setEnabled(False)
            self._refresh_feature_data(None)
            return
        self._active_profile_id = selected.id
        self.refresh_button.setEnabled(True)
        self._apply_header(checking_authentication_header(selected))
        # Local status is rendered first; network validation is a distinct background task.
        self._runner.submit(
            lambda: self._authentication.status(selected.id),
            self._local_status_loaded,
            self._show_error,
        )

    def _local_status_loaded(self, value: Any) -> None:
        status = value
        self._apply_header(build_authentication_header(status))
        self._runner.submit(
            lambda: self._authentication.validate(status.profile.id),
            self._validated,
            self._show_error,
        )

    def _validated(self, value: Any) -> None:
        status = value
        self._apply_header(build_authentication_header(status))
        if status.reusable:
            self._refresh_feature_data(status.profile.id)

    def open_profiles(self) -> None:
        self.profile_dialog.set_profiles(self._profile_summaries, self._active_profile_id)
        self.profile_dialog.open()

    def save_profile(self, request: SaveProfileRequest) -> None:
        def operation() -> ProfileSummary:
            if request.profile_id is not None:
                return self._profiles.update(request)
            return self._profiles.create(request)

        self._runner.submit(operation, self._profile_saved, self._show_error)

    def _profile_saved(self, value: Any) -> None:
        profile = value
        self._notify("프로필을 저장했습니다.")
        self.reload_profiles(profile.id)

    def clone_profile(self, profile_id: int) -> None:
        source = next((item for item in self._profile_summaries if item.id == profile_id), None)
        if source is None:
            self._show_error(
                ConfigurationError("profile.not_found", "Profile clone source missing")
            )
            return
        name, confirmed = self._profile_clone_name_provider(self, source.name)
        if not confirmed:
            return
        self._runner.submit(
            lambda: self._profiles.clone(profile_id, name.strip()),
            self._profile_cloned,
            self._show_error,
        )

    def _profile_cloned(self, value: Any) -> None:
        profile = value
        self._notify("프로필을 복제했습니다.")
        self.reload_profiles(profile.id)

    def delete_profile(self, profile_id: int) -> None:
        lifecycle = self._connection_lifecycle
        self._runner.submit(
            lambda: lifecycle.active(profile_id),
            lambda active: self._confirm_profile_delete_after_query(profile_id, active, lifecycle),
            self._show_error,
        )

    def _confirm_profile_delete_after_query(
        self,
        profile_id: int,
        active: ProfileConnections,
        lifecycle: ProfileConnectionLifecycleService,
    ) -> None:
        if active.has_active:
            if not self._active_delete_confirmation(self, active):
                return
            stop_active = True
        else:
            if not self._delete_confirmation(self, profile_id):
                return
            stop_active = False
        self._runner.submit(
            lambda: lifecycle.delete(profile_id, stop_active=stop_active),
            lambda _value: self._profile_deleted(profile_id),
            self._show_error,
        )

    def _profile_deleted(self, profile_id: int) -> None:
        self._notify("프로필을 삭제했습니다.")
        selected_id = None if profile_id == self._active_profile_id else self._active_profile_id
        self.reload_profiles(selected_id)

    def connect_profile(self, profile_id: int) -> None:
        self._apply_header(
            checking_authentication_header(
                next(item for item in self._profile_summaries if item.id == profile_id)
            )
        )
        self._runner.submit(
            lambda: self._profiles.use(profile_id),
            lambda profile: self._profile_selected(profile),
            self._show_error,
        )

    def _profile_selected(self, value: Any) -> None:
        profile = value
        self._active_profile_id = profile.id
        self.feature_data_revision += 1
        self.profile_dialog.close()
        self._profile_summaries = [
            ProfileSummary(
                id=item.id,
                name=item.name,
                region=item.region,
                account_id=item.account_id,
                user_id=item.user_id,
                mfa_arn=item.mfa_arn,
                is_default=item.id == profile.id,
                mfa_enabled=item.mfa_enabled,
            )
            for item in self._profile_summaries
        ]
        self.profile_dialog.set_profiles(self._profile_summaries, profile.id)
        self._apply_header(checking_authentication_header(profile))
        self._runner.submit(
            lambda: self._authentication.validate(profile.id),
            self._connected_identity_validated,
            self._show_error,
        )

    def _connected_identity_validated(self, value: Any) -> None:
        status = value
        self._apply_header(build_authentication_header(status))
        if not status.reusable:
            self._start_refresh(status.profile.id, self._refresh_completed)
        else:
            self._refresh_feature_data(status.profile.id)
            self._notify("프로필 연결을 확인했습니다.")

    def refresh_token(self) -> None:
        if self._active_profile_id is not None:
            self._start_refresh(self._active_profile_id, self._refresh_completed)

    def _start_refresh(
        self,
        profile_id: int,
        on_completed: Callable[[AuthenticationStatus], None],
    ) -> None:
        self.refresh_button.setEnabled(False)
        self._runner.submit(
            lambda: self._operations.start_refresh(profile_id),
            lambda result: self._refresh_started(result, on_completed),
            self._show_error,
        )

    def _refresh_started(
        self,
        value: Any,
        on_completed: Callable[[AuthenticationStatus], None],
    ) -> None:
        result = value
        if result.state is OperationState.SUCCEEDED and result.value is not None:
            self.refresh_button.setEnabled(True)
            on_completed(result.value)
            return
        if result.state is not OperationState.MFA_REQUIRED or result.challenge is None:
            self.refresh_button.setEnabled(True)
            if result.error is not None:
                self._show_error(result.error)
            return
        code = self._mfa_code_provider(self, result.challenge.device_arn)
        self._runner.submit(
            lambda: self._operations.resume(result.operation_id, code),
            lambda resumed: self._refresh_resumed(resumed, on_completed),
            self._show_error,
        )

    def _refresh_resumed(
        self,
        value: Any,
        on_completed: Callable[[AuthenticationStatus], None],
    ) -> None:
        result: OperationResult[AuthenticationStatus] = value
        self.refresh_button.setEnabled(True)
        if result.state is OperationState.SUCCEEDED and result.value is not None:
            on_completed(result.value)
        elif result.state is OperationState.CANCELLED:
            self._notify("MFA 인증을 취소했습니다.")
        elif result.error is not None:
            self._show_error(result.error)

    def _refresh_completed(self, status: AuthenticationStatus) -> None:
        self._apply_header(build_authentication_header(status))
        self.feature_data_revision += 1
        self._refresh_feature_data(status.profile.id)
        self._notify("토큰을 재발급했습니다.")

    def _refresh_feature_data(self, profile_id: int | None) -> None:
        profile = next(
            (item for item in self._profile_summaries if item.id == profile_id),
            None,
        )
        if self.ec2_page is not None:
            self.ec2_page.set_profile(profile_id, profile.region if profile is not None else None)
        if self.rds_page is not None:
            self.rds_page.set_profile(profile_id)
        if self.secrets_page is not None:
            self.secrets_page.set_profile(profile_id)
        if self.s3_page is not None:
            self.s3_page.set_profile(profile_id)

    def _prefill_rds_endpoint(self, host: str, port: int) -> None:
        if self.rds_page is None:
            return
        self.rds_page.prefill_endpoint(host, port)
        self.pages.setCurrentWidget(self.rds_page)
        self._notify("RDS endpoint를 편집기에 복사했습니다. 세션명과 중계 EC2를 확인해 저장하세요.")

    def _apply_header(self, view_model: AuthenticationHeaderViewModel) -> None:
        self.profile_name.setText(view_model.profile_name)
        indicator = "● " if view_model.authenticated else "○ "
        self.auth_state.setText(indicator + view_model.state_text)
        self.account_id.setText(view_model.account_id)
        self.iam_user.setText(view_model.user_id)
        self.token_expiry.setText(view_model.expiry_text)

    def update_active_tunnels(self, tunnels: list[ActiveTunnelSummaryViewModel]) -> None:
        """Refresh the dashboard projection without loading tunnel state in the view."""

        if not tunnels:
            self.tunnel_summary.setText("실행 중인 터널이 없습니다.")
            self.dashboard_tunnels.clear()
            self.dashboard_tunnels.hide()
            return
        self.tunnel_summary.setText(f"{len(tunnels)}개 실행 중")
        self.dashboard_tunnels.clear()
        for summary in tunnels:
            item = QListWidgetItem()
            row = ActiveTunnelRow(summary)
            row.copied.connect(
                lambda _address: self._notify(
                    "로컬 주소를 복사했습니다. 30초 후 클립보드에서 제거합니다."
                )
            )
            row.stop_requested.connect(self._stop_dashboard_tunnel)
            row_height = max(54, row.sizeHint().height() + 8)
            item.setSizeHint(QSize(row.sizeHint().width(), row_height))
            self.dashboard_tunnels.addItem(item)
            self.dashboard_tunnels.setItemWidget(item, row)
        visible_rows = min(3, len(tunnels))
        visible_height = sum(
            self.dashboard_tunnels.sizeHintForRow(index) for index in range(visible_rows)
        )
        self.dashboard_tunnels.setFixedHeight(visible_height + 6)
        self.dashboard_tunnels.show()

    def _stop_dashboard_tunnel(self, operation_id: str) -> None:
        if self.rds_page is not None:
            self.rds_page.stop_operation(operation_id)

    def _show_error(self, error: ApplicationError) -> None:
        self.last_error = map_error(error)
        self._record_activity(
            result="failed",
            message_code=error.message_code,
            correlation_id=error.correlation_id,
            aws_service=error.aws_service,
            aws_action=error.aws_action,
            retryable=error.retryable,
            masked_detail=error.technical_cause,
        )
        self.refresh_button.setEnabled(self._active_profile_id is not None)
        if self.last_error.presentation is GuiErrorPresentation.FIELD:
            self._show_field_error(self.last_error)
        elif self.last_error.presentation is GuiErrorPresentation.TOAST:
            self._show_toast(f"{self.last_error.title}: {self.last_error.message}")
        else:
            self._show_error_dialog(self.last_error)

    def _show_field_error(self, view_model: GuiErrorViewModel) -> None:
        field = self.findChild(QWidget, view_model.field_name or "")
        if field is None:
            self._show_error_dialog(view_model)
            return
        field.setProperty("application_error", True)
        field.setToolTip(view_model.message)
        field.style().unpolish(field)
        field.style().polish(field)
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        self._show_toast(f"{view_model.title}: {view_model.message}")

    def _show_error_dialog(self, view_model: GuiErrorViewModel) -> None:
        dialog = QMessageBox(
            QMessageBox.Icon.Critical,
            view_model.title,
            view_model.message,
            QMessageBox.StandardButton.Ok,
            self,
        )
        dialog.setObjectName("application_error_dialog")
        dialog.setModal(True)
        dialog.open()
        self.error_dialog = dialog

    def _notify(self, message: str) -> None:
        self._record_activity(result="notice", message_code="gui.notice")
        self._show_toast(message)

    def _show_toast(self, message: str) -> None:
        self.toast.setText(message)
        self.toast.adjustSize()
        self.toast.move(max(12, self.width() - self.toast.width() - 24), self.height() - 58)
        self.toast.show()
        QTimer.singleShot(3500, self.toast.hide)

    def _record_activity(
        self,
        *,
        result: str,
        message_code: str,
        correlation_id: str | None = None,
        operation_id: str | None = None,
        aws_service: str | None = None,
        aws_action: str | None = None,
        retryable: bool | None = None,
        masked_detail: str | None = None,
    ) -> None:
        if self._activity_logs is None:
            return
        activity_logs = self._activity_logs
        targets = ("dashboard", "ec2", "rds", "secrets", "s3", "logs")
        current = self.pages.currentIndex()
        target = targets[current] if 0 <= current < len(targets) else "window"

        def record() -> None:
            fields: dict[str, Any] = {
                "feature": "gui",
                "target": target,
                "result": result,
                "message_code": message_code,
                "correlation_id": correlation_id,
            }
            optional = {
                "operation_id": operation_id,
                "aws_service": aws_service,
                "aws_action": aws_action,
                "retryable": retryable,
                "masked_detail": masked_detail,
            }
            fields.update({key: value for key, value in optional.items() if value is not None})
            activity_logs.record(**fields)

        self._runner.submit(
            record,
            lambda _value: self._activity_recorded(),
            self._activity_record_failed,
        )

    def _activity_recorded(self) -> None:
        self.activity_log_error_code = None

    def _activity_record_failed(self, error: ApplicationError) -> None:
        # Logging must never take down or recursively notify the GUI.
        self.activity_log_error_code = error.message_code

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._allow_close:
            event.accept()
            return
        if self._closing:
            event.ignore()
            return
        self._closing = True
        event.ignore()

        def finish() -> None:
            self._allow_close = True
            QTimer.singleShot(0, self.close)

        def stop_tunnels() -> None:
            if self.rds_page is None:
                finish()
            else:
                self.rds_page.shutdown(finish)

        if self.s3_page is None:
            stop_tunnels()
        else:
            self.s3_page.shutdown(stop_tunnels)


def _confirm_profile_delete(parent: QWidget, _profile_id: int) -> bool:
    answer = QMessageBox.question(
        parent,
        "프로필 삭제",
        "선택한 프로필을 삭제할까요? 저장된 토큰도 함께 삭제됩니다.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def _ask_profile_clone_name(parent: QWidget, source_name: str) -> tuple[str, bool]:
    return QInputDialog.getText(
        parent,
        "프로필 복제",
        "새 프로필 ID",
        QLineEdit.EchoMode.Normal,
        f"{source_name} 복사본",
    )


def _confirm_active_profile_delete(parent: QWidget, active: ProfileConnections) -> bool:
    answer = QMessageBox.question(
        parent,
        "활성 연결 종료 후 프로필 삭제",
        (
            f"EC2 세션 {active.ec2_count}개와 RDS 터널 {active.rds_count}개가 실행 중입니다.\n"
            "모든 연결을 종료한 뒤 프로필을 삭제할까요?"
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    return answer == QMessageBox.StandardButton.Yes


def _confirm_tunnel_delete(parent: QWidget, _tunnel_id: int) -> bool:
    answer = QMessageBox.question(
        parent,
        "RDS 터널 세션 삭제",
        "선택한 저장 세션을 삭제할까요?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes
