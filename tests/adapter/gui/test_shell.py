from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, Qt, QThread, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QWidget,
)

from aws_connect.application.authentication_service import AuthenticationStatus
from aws_connect.application.connection_lifecycle import ProfileConnections
from aws_connect.application.operations import (
    CancellationToken,
    MfaChallenge,
    OperationResult,
    OperationState,
)
from aws_connect.application.profile_service import ProfileSummary, SaveProfileRequest
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.presentation.gui.errors import GuiErrorPresentation
from aws_connect.presentation.gui.tasks import ApplicationTask, GuiTaskRunner
from aws_connect.presentation.gui.view_models import (
    build_authentication_header,
    empty_authentication_header,
)
from aws_connect.presentation.gui.window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _profile(
    profile_id: int, name: str, *, default: bool, mfa_enabled: bool = True
) -> ProfileSummary:
    return ProfileSummary(
        id=profile_id,
        name=name,
        region="ap-northeast-2",
        account_id="123456789012",
        user_id=f"user-{profile_id}",
        mfa_arn=f"arn:aws:iam::123456789012:mfa/user-{profile_id}",
        is_default=default,
        mfa_enabled=mfa_enabled,
    )


class ImmediateTaskRunner:
    def submit(
        self,
        operation: Callable[[], object],
        on_success: Callable[[Any], None],
        on_error: Callable[[ApplicationError], None],
    ) -> None:
        try:
            on_success(operation())
        except ApplicationError as error:
            on_error(error)


class FakeProfiles:
    def __init__(self) -> None:
        self.items = [_profile(1, "개발계", default=True), _profile(2, "운영계", default=False)]
        self.deleted: list[int] = []
        self.cloned: list[tuple[int, str]] = []

    def list(self) -> list[ProfileSummary]:
        return self.items

    def use(self, profile_id: int) -> ProfileSummary:
        selected = next(item for item in self.items if item.id == profile_id)
        self.items = [
            _profile(
                item.id,
                item.name,
                default=item.id == profile_id,
                mfa_enabled=item.mfa_enabled,
            )
            for item in self.items
        ]
        return _profile(
            selected.id,
            selected.name,
            default=True,
            mfa_enabled=selected.mfa_enabled,
        )

    def create(self, request: SaveProfileRequest) -> ProfileSummary:
        created = _profile(
            3,
            request.name,
            default=False,
            mfa_enabled=request.mfa_enabled is not False,
        )
        self.items.append(created)
        return created

    def update(self, request: SaveProfileRequest) -> ProfileSummary:
        assert request.profile_id is not None
        current = next(item for item in self.items if item.id == request.profile_id)
        updated = _profile(
            request.profile_id,
            request.name,
            default=False,
            mfa_enabled=(
                request.mfa_enabled if request.mfa_enabled is not None else current.mfa_enabled
            ),
        )
        self.items = [updated if item.id == request.profile_id else item for item in self.items]
        return updated

    def clone(self, profile_id: int, name: str) -> ProfileSummary:
        self.cloned.append((profile_id, name))
        cloned = _profile(3, name, default=False)
        self.items.append(cloned)
        return cloned

    def delete(self, profile_id: int) -> None:
        self.deleted.append(profile_id)
        self.items = [item for item in self.items if item.id != profile_id]


class FakeAuthentication:
    def __init__(self, profiles: FakeProfiles) -> None:
        self.profiles = profiles
        self.calls: list[tuple[str, int]] = []

    def status(self, profile_id: int) -> AuthenticationStatus:
        self.calls.append(("status", profile_id))
        profile = next(item for item in self.profiles.items if item.id == profile_id)
        return AuthenticationStatus(profile, "MFA_REQUIRED", None, False)

    def validate(self, profile_id: int) -> AuthenticationStatus:
        self.calls.append(("validate", profile_id))
        return self.status(profile_id)


class FakeOperations:
    def __init__(self, profiles: FakeProfiles) -> None:
        self.profiles = profiles
        self.start_calls: list[int] = []
        self.resume_calls: list[tuple[str, str | None]] = []

    def start_refresh(self, profile_id: int) -> OperationResult[AuthenticationStatus]:
        self.start_calls.append(profile_id)
        profile = next(item for item in self.profiles.items if item.id == profile_id)
        if not profile.mfa_enabled:
            return OperationResult(
                "operation-1",
                OperationState.SUCCEEDED,
                value=AuthenticationStatus(
                    profile,
                    "READY",
                    datetime.now(UTC) + timedelta(hours=12),
                    True,
                ),
            )
        return OperationResult(
            "operation-1",
            OperationState.MFA_REQUIRED,
            challenge=MfaChallenge(
                "operation-1",
                profile_id,
                "arn:aws:iam::123456789012:mfa/test",
                datetime.now(UTC) + timedelta(minutes=5),
            ),
        )

    def resume(self, operation_id: str, code: str | None) -> OperationResult[AuthenticationStatus]:
        self.resume_calls.append((operation_id, code))
        profile = next(item for item in self.profiles.items if item.is_default)
        status = AuthenticationStatus(
            profile,
            "READY",
            datetime.now(UTC) + timedelta(hours=12),
            True,
        )
        return OperationResult(operation_id, OperationState.SUCCEEDED, value=status)


def _window(
    *,
    auto_start: bool = True,
    connection_lifecycle=None,
    active_delete_confirmation=None,
    profile_clone_name_provider=None,
) -> tuple[MainWindow, FakeProfiles, FakeOperations]:
    _app()
    profiles = FakeProfiles()
    authentication = FakeAuthentication(profiles)
    operations = FakeOperations(profiles)
    lifecycle = connection_lifecycle or FakeConnectionLifecycle(
        ProfileConnections(2, 0, 0), profiles
    )
    window = MainWindow(
        profiles,  # type: ignore[arg-type]
        authentication,  # type: ignore[arg-type]
        operations,  # type: ignore[arg-type]
        task_runner=ImmediateTaskRunner(),  # type: ignore[arg-type]
        mfa_code_provider=lambda _parent, _arn: "123456",
        delete_confirmation=lambda _parent, _profile_id: True,
        active_delete_confirmation=active_delete_confirmation,
        profile_clone_name_provider=profile_clone_name_provider,
        connection_lifecycle=lifecycle,  # type: ignore[arg-type]
        auto_start=auto_start,
    )
    return window, profiles, operations


class FakeConnectionLifecycle:
    def __init__(self, active: ProfileConnections, profiles: FakeProfiles | None = None) -> None:
        self.active_value = active
        self.profiles = profiles
        self.active_calls: list[int] = []
        self.delete_calls: list[tuple[int, bool]] = []

    def active(self, profile_id: int) -> ProfileConnections:
        self.active_calls.append(profile_id)
        return self.active_value

    def delete(self, profile_id: int, *, stop_active: bool) -> None:
        self.delete_calls.append((profile_id, stop_active))
        if self.profiles is not None:
            self.profiles.delete(profile_id)


def test_header_view_models_cover_missing_mfa_and_ready_states() -> None:
    assert empty_authentication_header().state_text == "인증정보 없음"
    profile = _profile(1, "개발계", default=True)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    mfa = build_authentication_header(
        AuthenticationStatus(profile, "MFA_REQUIRED", None, False), now
    )
    ready = build_authentication_header(
        AuthenticationStatus(profile, "READY", now + timedelta(hours=1), True), now
    )

    assert mfa.state_text == "MFA 인증 필요"
    assert ready.state_text == "인증됨"
    assert ready.account_id == "1234 5678 9012"
    assert ready.expiry_text == "01:00:00 남음"


def test_profile_change_resumes_mfa_and_refreshes_header_and_feature_data() -> None:
    window, _profiles, operations = _window()
    window.reload_profiles()

    window.connect_profile(2)

    assert window.profile_name.text() == "운영계"
    assert window.iam_user.text() == "user-2"
    assert "인증됨" in window.auth_state.text()
    assert window.feature_data_revision == 2
    assert operations.resume_calls == [("operation-1", "123456")]


def test_profile_crud_fields_mask_credentials_and_errors_do_not_close_app() -> None:
    window, profiles, _operations = _window(auto_start=False)
    assert not window.profile_button.icon().isNull()
    assert window.profile_dialog.access_key.echoMode() is QLineEdit.EchoMode.Password
    assert window.profile_dialog.secret_key.echoMode() is QLineEdit.EchoMode.Password
    window.profile_dialog.set_profiles(profiles.items, 1)
    assert window.profile_dialog.findChild(QLineEdit, "profile_mfa_arn") is None
    assert window.profile_dialog.findChild(QCheckBox, "profile_mfa_enabled") is not None
    assert window.profile_dialog.access_key.text() == ""
    assert window.profile_dialog.secret_key.text() == ""
    assert "등록됨" in window.profile_dialog.access_key.placeholderText()
    assert "등록됨" in window.profile_dialog.secret_key.placeholderText()

    window._show_error(ConfigurationError("profile.not_found", "local detail"))
    window.delete_profile(2)

    assert window.last_error is not None
    assert window.last_error.message_code == "profile.not_found"
    assert window.last_error.presentation is GuiErrorPresentation.DIALOG
    assert window.error_dialog is not None
    assert not window.isHidden() or not window.isVisible()
    assert profiles.deleted == [2]


def test_profile_dialog_omits_implementation_mfa_and_preserves_masked_credentials() -> None:
    window, profiles, _operations = _window(auto_start=False)
    window.profile_dialog.set_profiles(profiles.items, 1)
    captured: list[SaveProfileRequest] = []
    window.profile_dialog.save_requested.connect(captured.append)

    window.profile_dialog._request_save()

    assert captured[0].profile_id == 1
    assert captured[0].mfa_arn is None
    assert captured[0].mfa_enabled
    assert captured[0].access_key is None
    assert captured[0].secret_key is None
    assert window.profile_dialog.findChild(QLabel, "profile_sequence") is None

    window.profile_dialog.new_profile()

    assert window.profile_dialog.findChild(QLabel, "profile_editor_title") is None
    assert not hasattr(window.profile_dialog, "sequence")
    assert "입력" in window.profile_dialog.access_key.placeholderText()


def test_profile_dialog_new_credentials_keep_existing_save_signal_contract() -> None:
    window, _profiles, _operations = _window(auto_start=False)
    captured: list[SaveProfileRequest] = []
    window.profile_dialog.save_requested.connect(captured.append)
    window.profile_dialog.name.setText("신규")
    window.profile_dialog.account.setText("123456789012")
    window.profile_dialog.user.setText("new-user")
    window.profile_dialog.access_key.setText("AKIAEXAMPLE")
    window.profile_dialog.secret_key.setText("new-secret")  # pragma: allowlist secret

    window.profile_dialog._request_save()

    assert captured[0].profile_id is None
    assert captured[0].mfa_arn is None
    assert captured[0].mfa_enabled
    assert captured[0].access_key == "AKIAEXAMPLE"
    assert captured[0].secret_key == "new-secret"  # pragma: allowlist secret


def test_profile_eyes_only_toggle_new_input_and_reset_on_selection() -> None:
    window, profiles, _operations = _window(auto_start=False)
    dialog = window.profile_dialog
    dialog.set_profiles(profiles.items, 1)
    for field in (dialog.access_key, dialog.secret_key):
        eye = field.actions()[0]
        assert not eye.isEnabled()
        assert not field.text()
        field.setText(os.urandom(12).hex())
        eye.trigger()
        assert field.echoMode() == QLineEdit.EchoMode.Normal
        eye.trigger()
        assert field.echoMode() == QLineEdit.EchoMode.Password
        eye.trigger()
    dialog.profile_list.setCurrentRow(1)
    for field in (dialog.access_key, dialog.secret_key):
        assert not field.text()
        assert field.echoMode() == QLineEdit.EchoMode.Password
        assert not field.actions()[0].isEnabled()
        assert not field.actions()[0].isChecked()
    assert dialog.save_button.text() == "저장"
    assert dialog.connect_button.text() == "이 프로필로 연결"
    assert dialog.new_button.text() == "+ 새 프로필"
    assert not hasattr(dialog, "clone_button")
    assert not hasattr(dialog, "delete_button")


def test_profile_row_menu_delete_targets_clicked_row() -> None:
    window, profiles, _operations = _window(auto_start=False)
    dialog = window.profile_dialog
    dialog.set_profiles(profiles.items, 1)
    row = dialog.profile_list.itemWidget(dialog.profile_list.item(1))
    row.findChild(QPushButton, "profile_row_more").click()
    menu = dialog.findChild(QMenu, "profile_row_menu")
    menu.actions()[1].trigger()
    assert profiles.deleted == [2]


def test_profile_without_mfa_connects_without_requesting_a_code() -> None:
    window, profiles, operations = _window(auto_start=False)
    profiles.items[1] = _profile(2, "운영계", default=False, mfa_enabled=False)
    window.reload_profiles()

    window.connect_profile(2)

    assert operations.start_calls == [2]
    assert operations.resume_calls == []
    assert "인증됨" in window.auth_state.text()


def test_profile_dialog_persists_disabled_mfa_selection() -> None:
    window, profiles, _operations = _window(auto_start=False)
    profiles.items[1] = _profile(2, "운영계", default=False, mfa_enabled=False)
    window.profile_dialog.set_profiles(profiles.items, 2)
    captured: list[SaveProfileRequest] = []
    window.profile_dialog.save_requested.connect(captured.append)

    assert not window.profile_dialog.mfa_enabled.isChecked()
    window.profile_dialog._request_save()

    assert captured[0].mfa_enabled is False


def test_profile_new_draft_selection_details_and_clone_flow() -> None:
    window, profiles, _operations = _window(
        auto_start=False,
        profile_clone_name_provider=lambda _parent, source: (f"{source}-copy", True),
    )
    window.reload_profiles()

    window.profile_dialog.new_button.click()
    draft = window.profile_dialog.profile_list.currentItem()
    assert draft is not None
    assert draft.data(Qt.ItemDataRole.UserRole) is None
    assert "저장되지 않음" in draft.data(Qt.ItemDataRole.AccessibleTextRole)
    window.profile_dialog.name.setText("임시 프로필")
    window.profile_dialog.account.setText("123456789012")
    window.profile_dialog.user.setText("new-user")
    window.profile_dialog.access_key.setText("AKIAEXAMPLE")
    window.profile_dialog.secret_key.setText("new-secret")  # pragma: allowlist secret
    window.profile_dialog.save_button.click()
    assert any(item.name == "임시 프로필" for item in profiles.items)

    window.profile_dialog.profile_list.setCurrentRow(0)
    assert window.profile_dialog.name.text() == "개발계"
    assert window.profile_dialog.account.text() == "123456789012"
    dialog = window.profile_dialog
    row = dialog.profile_list.itemWidget(dialog.profile_list.currentItem())
    row.findChild(QPushButton, "profile_row_more").click()
    menu = dialog.findChild(QMenu, "profile_row_menu")
    assert [action.text() for action in menu.actions()] == ["프로필 복제", "프로필 삭제"]
    menu.actions()[0].trigger()

    assert profiles.cloned == [(1, "개발계-copy")]


def test_unsaved_profile_delete_discards_draft_without_service_delete() -> None:
    window, profiles, _operations = _window(auto_start=False)
    window.profile_dialog.set_profiles(profiles.items, 1)
    window.profile_dialog.new_profile()

    dialog = window.profile_dialog
    row = dialog.profile_list.itemWidget(dialog.profile_list.currentItem())
    row.findChild(QPushButton, "profile_row_more").click()
    menu = dialog.findChild(QMenu, "profile_row_menu")
    assert not menu.actions()[0].isEnabled()
    menu.actions()[1].trigger()

    assert profiles.deleted == []
    assert window.profile_dialog.profile_list.currentItem() is not None
    assert (
        window.profile_dialog.profile_list.currentItem().data(Qt.ItemDataRole.UserRole) is not None
    )
    assert window.profile_dialog.findChild(QPushButton, "profile_close_button") is None


def test_profile_delete_with_active_connections_requires_explicit_stop_choice() -> None:
    lifecycle = FakeConnectionLifecycle(ProfileConnections(2, 1, 1))
    confirmations: list[ProfileConnections] = []
    window, _profiles, _operations = _window(
        auto_start=False,
        connection_lifecycle=lifecycle,
        active_delete_confirmation=lambda _parent, active: confirmations.append(active) or False,
    )

    window.delete_profile(2)

    assert lifecycle.active_calls == [2]
    assert confirmations == [lifecycle.active_value]
    assert lifecycle.delete_calls == []


def test_profile_delete_routes_confirmed_active_and_inactive_choices_to_lifecycle() -> None:
    active = FakeConnectionLifecycle(ProfileConnections(2, 1, 0))
    active_window, _profiles, _operations = _window(
        auto_start=False,
        connection_lifecycle=active,
        active_delete_confirmation=lambda _parent, _connections: True,
    )

    active_window.delete_profile(2)

    inactive = FakeConnectionLifecycle(ProfileConnections(2, 0, 0))
    inactive_window, _profiles, _operations = _window(
        auto_start=False,
        connection_lifecycle=inactive,
    )
    inactive_window.delete_profile(2)

    assert active.delete_calls == [(2, True)]
    assert inactive.delete_calls == [(2, False)]


def test_gui_entry_point_wires_composed_connection_lifecycle(monkeypatch) -> None:
    from aws_connect import gui_main

    services = Mock()
    application = Mock()
    application.exec.return_value = 0
    window = Mock()
    monkeypatch.setattr(gui_main, "build_application_services", lambda: services)
    monkeypatch.setattr(gui_main, "QApplication", lambda _argv: application)
    captured: dict[str, object] = {}

    def capture(*_args, **kwargs):
        captured.update(kwargs)
        return window

    monkeypatch.setattr(gui_main, "MainWindow", capture)
    assert gui_main.main([]) == 0
    assert captured["connection_lifecycle"] is services.connection_lifecycle
    app_icon = application.setWindowIcon.call_args.args[0]
    assert not app_icon.isNull()
    window.show.assert_called_once_with()


def test_dashboard_has_no_active_tunnel_summary() -> None:
    window, _profiles, _operations = _window(auto_start=False)
    assert window.findChild(QLabel, "active_tunnel_summary") is None
    assert window.findChild(QWidget, "dashboard_active_tunnels") is None


def test_dashboard_matches_mockup_card_content_and_routes() -> None:
    window, _profiles, _operations = _window(auto_start=False)

    expected = {
        "ec2": ("EC2 인스턴스에 SSM으로 접속하는 기능입니다.", "EC2 접속하기", 1),
        "rds": ("SSM 포트포워딩을 통해 RDS에 연결하는 기능입니다.", "RDS 터널 열기", 2),
        "secrets": ("Secrets를 조회하고 안전하게 관리하는 기능입니다.", "Secrets 열기", 3),
        "s3": ("S3 버킷을 탐색하고 파일을 업로드하는 기능입니다.", "S3 파일 열기", 4),
    }
    cards = window.findChildren(QFrame, "feature_card")

    assert len(cards) == 4
    for card in cards:
        route = str(card.property("feature"))
        subtitle, action, page = expected[route]
        assert card.findChild(QWidget, "feature_description").property("text") == subtitle
        button = card.findChild(QPushButton, f"dashboard_{route}_button")
        assert button is not None
        assert button.text() == action
        assert not button.icon().isNull()
        button.click()
        assert window.pages.currentIndex() == page

    for index, title in enumerate(
        ("대시보드", "EC2 접속", "RDS 터널", "Secrets", "S3 파일", "실행 로그")
    ):
        button = window.findChild(QPushButton, f"nav_{index}")
        assert button is not None
        assert button.text() == title
        assert not button.icon().isNull()

    assert window.profile_button.toolTip() == "프로필 관리"
    assert window.profile_button.accessibleName() == "프로필 관리"
    assert not window.profile_button.icon().isNull()
    assert window.refresh_button.toolTip() == "토큰 재발급"
    assert window.refresh_button.accessibleName() == "토큰 재발급"
    assert not window.refresh_button.icon().isNull()


def test_gui_error_presentation_kind_controls_dialog_toast_and_field() -> None:
    window, _profiles, _operations = _window(auto_start=False)

    window._show_error(ConfigurationError("profile.region.invalid", "detail"))
    assert window.last_error is not None
    assert window.last_error.presentation is GuiErrorPresentation.FIELD
    assert window.profile_dialog.region.property("application_error") is True
    assert not window.toast.isHidden()
    window.profile_dialog.region.setText("us-east-1")
    assert window.profile_dialog.region.property("application_error") is False

    retryable = ConfigurationError("temporary", "detail", retryable=True)
    window._show_error(retryable)
    assert window.last_error is not None
    assert window.last_error.presentation is GuiErrorPresentation.TOAST

    window._show_error(ConfigurationError("profile.not_found", "detail"))
    assert window.last_error is not None
    assert window.last_error.presentation is GuiErrorPresentation.DIALOG
    assert window.error_dialog is not None
    assert window.error_dialog.objectName() == "application_error_dialog"
    window.error_dialog.done(QMessageBox.StandardButton.Ok)


def test_shell_connects_auth_free_logs_page_and_records_allowlisted_gui_event() -> None:
    _app()
    profiles = FakeProfiles()
    activity = Mock()
    settings = Mock()
    settings.get.return_value = __import__(
        "aws_connect.domain.app_settings", fromlist=["AppSettings"]
    ).AppSettings(
        __import__("pathlib").Path("logs"),
        __import__("aws_connect.domain.app_settings", fromlist=["LogLevel"]).LogLevel.INFO,
    )
    activity.recent.return_value = []
    window = MainWindow(
        profiles,  # type: ignore[arg-type]
        FakeAuthentication(profiles),  # type: ignore[arg-type]
        FakeOperations(profiles),  # type: ignore[arg-type]
        settings=settings,
        activity_logs=activity,
        diagnostic_logs=Mock(),
        connection_lifecycle=Mock(),
        task_runner=ImmediateTaskRunner(),  # type: ignore[arg-type]
        auto_start=False,
    )

    assert window.logs_page is not None
    window.pages.setCurrentIndex(5)
    window._notify("This user-facing text must never be serialized")

    activity.record.assert_not_called()
    activity.list_recent.return_value = []
    window.pages.setCurrentIndex(0)
    activity.list_recent.assert_called_once_with(5)


def test_1024_by_720_shell_keeps_critical_regions_inside_viewport() -> None:
    app = _app()
    window, _profiles, _operations = _window(auto_start=False)
    window.resize(1024, 720)
    window.show()
    app.processEvents()

    central = window.centralWidget().rect()
    critical: tuple[QWidget | None, ...] = (
        window.findChild(QFrame, "authentication_header"),
        window.findChild(QFrame, "navigation"),
        window.dashboard_page,
        window.profile_button,
        window.refresh_button,
    )
    for widget in critical:
        assert widget is not None
        top_left = widget.mapTo(window.centralWidget(), widget.rect().topLeft())
        bottom_right = widget.mapTo(window.centralWidget(), widget.rect().bottomRight())
        assert central.contains(top_left)
        assert central.contains(bottom_right)

    assert not window.profile_button.geometry().intersects(window.refresh_button.geometry())
    header_items = (
        window.profile_name,
        window.auth_state,
        window.account_id,
        window.iam_user,
        window.token_expiry,
        window.profile_button,
        window.refresh_button,
    )
    for index, first in enumerate(header_items):
        assert first.width() > 0 and first.height() > 0
        for second in header_items[index + 1 :]:
            assert not first.geometry().intersects(second.geometry())
    assert window.auth_state.geometry().left() > window.token_expiry.geometry().left()
    value_tops = {
        window.profile_name.geometry().top(),
        window.account_id.geometry().top(),
        window.iam_user.geometry().top(),
        window.token_expiry.geometry().top(),
        window.auth_state.geometry().top(),
    }
    assert len(value_tops) == 1


def test_desktop_shell_uses_mockup_geometry_tokens_exactly() -> None:
    app = _app()
    window, _profiles, _operations = _window(auto_start=False)
    window.resize(1424, 894)
    window.show()
    app.processEvents()

    shell = window.findChild(QFrame, "app_shell")
    header = window.findChild(QFrame, "authentication_header")
    navigation = window.findChild(QFrame, "navigation")
    dashboard = window.findChild(QWidget, "dashboard")

    assert shell is not None and shell.size().width() == 1424
    assert shell.size().height() == 894
    assert shell.pos().x() == 0 and shell.pos().y() == 0
    assert header is not None and header.height() == 92
    assert navigation is not None and navigation.width() == 220
    assert window.profile_button.size().width() == 42
    assert window.profile_button.size().height() == 42
    assert window.refresh_button.size() == window.profile_button.size()
    assert dashboard is not None and dashboard.layout() is not None
    margins = dashboard.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
        24,
        22,
        24,
        24,
    )
    window.close()


def test_task_runner_executes_blocking_call_off_gui_thread() -> None:
    app = _app()
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    runner = GuiTaskRunner(pool)
    loop = QEventLoop()
    observed: list[bool] = []

    def operation() -> object:
        return QThread.currentThread() is app.thread()

    runner.submit(operation, lambda value: (observed.append(bool(value)), loop.quit()), pytest_fail)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    pool.waitForDone(3000)

    assert observed == [False]


def test_cancelled_gui_task_does_not_execute_or_publish_success() -> None:
    token = CancellationToken()
    called: list[str] = []
    task = ApplicationTask(lambda: called.append("operation"), token)
    task.signals.succeeded.connect(lambda _value: called.append("success"))

    token.cancel()
    task.run()

    assert called == []


def test_unexpected_gui_task_error_never_serializes_exception_message() -> None:
    token = CancellationToken()
    failures: list[ApplicationError] = []
    raw = "secret_access_key=must-not-escape"
    task = ApplicationTask(lambda: (_ for _ in ()).throw(RuntimeError(raw)), token)
    task.signals.failed.connect(failures.append)

    task.run()

    assert failures[0].message_code == "gui.task.unexpected"
    assert failures[0].technical_cause == "RuntimeError"
    assert raw not in repr(failures[0])


def pytest_fail(error: ApplicationError) -> None:
    raise AssertionError(error.message_code)
