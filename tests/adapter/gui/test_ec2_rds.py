from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QFrame, QHeaderView, QLabel, QMenu, QPushButton, QWidget

from aws_connect.application.ec2_service import Ec2Target, ExternalSessionHandle
from aws_connect.application.operations import OperationResult, OperationState
from aws_connect.application.ports import RdsEndpoint
from aws_connect.application.rds_tunnel_service import (
    SaveTunnelSessionRequest,
    StartTunnelRequest,
    TunnelConnectionResult,
    TunnelHandle,
    TunnelOwner,
)
from aws_connect.domain.errors import ApplicationError, ConfigurationError, PluginExecutionError
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession
from aws_connect.presentation.gui.ec2_rds import Ec2Page, RdsPage
from aws_connect.presentation.gui.styles import APP_STYLE
from aws_connect.presentation.gui.window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class ImmediateRunner:
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


class QueuedRunner:
    def __init__(self) -> None:
        self.pending: list[
            tuple[
                Callable[[], object],
                Callable[[Any], None],
                Callable[[ApplicationError], None],
            ]
        ] = []

    def submit(
        self,
        operation: Callable[[], object],
        on_success: Callable[[Any], None],
        on_error: Callable[[ApplicationError], None],
    ) -> None:
        self.pending.append((operation, on_success, on_error))


class FakeEc2:
    def __init__(self) -> None:
        self.targets = [
            Ec2Target("i-0123456789abcdef0", "web-dev", "10.0.0.10", "Linux", "Online"),
            Ec2Target("i-abcdef01234567890", "worker", "10.0.0.20", "Linux", "Online"),
        ]
        self.connect_calls: list[tuple[str, int | None, str | None]] = []
        self.list_calls: list[tuple[int | None, str]] = []
        self.reap_calls = 0
        self.reaped: list[ExternalSessionHandle] = []
        self.favorite_calls: list[tuple[str, bool, int | None, str | None]] = []
        self.power_calls: list[tuple[str, str, int | None, str | None]] = []
        self.fail = False

    def list_targets(self, selector=None):
        if self.fail:
            raise ConfigurationError("profile.not_found", "test")
        return self.targets

    def list_targets_in_region(self, selector, region):
        self.list_calls.append((selector, region))
        return self.list_targets(selector)

    def connect_external(self, instance_id, selector=None, *, region=None, context=None):
        self.connect_calls.append((instance_id, selector, region))
        return ExternalSessionHandle(
            "ec2-operation",
            7001,
            "session-ec2",
            instance_id,
            int(selector or 1),
            OperationState.RUNNING,
        )

    def external_sessions(self, profile_id=None):
        return [self.connect_external("i-0123456789abcdef0", 1)]

    def reap_external_sessions(self):
        self.reap_calls += 1
        return self.reaped

    def set_favorite(self, instance_id, favorite, selector=None, *, region=None):
        self.favorite_calls.append((instance_id, favorite, selector, region))

    def start_instance(self, instance_id, selector=None, *, region=None):
        self.power_calls.append(("start", instance_id, selector, region))
        return object()

    def reboot_instance(self, instance_id, selector=None, *, region=None):
        self.power_calls.append(("reboot", instance_id, selector, region))
        return object()


class FakeSavedSessions:
    def __init__(self) -> None:
        self.item = TunnelSession(
            3,
            1,
            "개발 DB",
            "db.example.internal",
            3306,
            13306,
            TargetMode.SELECT,
        )
        self.saved: list[SaveTunnelSessionRequest] = []
        self.cloned: list[tuple[int, str, int]] = []
        self.deleted: list[int] = []

    def list(self, profile=None):
        return [self.item]

    def create(self, request):
        self.saved.append(request)
        return self.item

    def update(self, request):
        self.saved.append(request)
        self.item = TunnelSession(
            request.tunnel_id,
            request.profile,
            request.name,
            request.host,
            request.remote_port,
            request.local_port,
            request.target_mode,
            request.target_instance_id,
        )
        return self.item

    def rename(self, selector, name, profile=None):
        from dataclasses import replace

        self.item = replace(self.item, name=name)
        return self.item

    def clone(self, selector, name, profile=None):
        self.cloned.append((selector, name, profile))
        return TunnelSession(
            4,
            self.item.profile_id,
            name,
            self.item.host,
            self.item.remote_port,
            self.item.local_port,
            self.item.target_mode,
            self.item.target_instance_id,
        )

    def delete(self, selector, profile=None):
        self.deleted.append(selector)


class FakeTunnels:
    def __init__(self, saved: FakeSavedSessions) -> None:
        self.saved = saved
        self.start_calls: list[StartTunnelRequest] = []
        self.stop_calls: list[str] = []
        self.stop_all_calls = 0
        self.active: list[TunnelConnectionResult] = []

    def local_port_available(self, port: int) -> bool:
        return True

    def start_managed(self, request):
        self.start_calls.append(request)
        result = TunnelConnectionResult(
            self.saved.item,
            "i-0123456789abcdef0",
            TunnelHandle(
                "session-rds",
                TunnelOwner.GUI,
                OperationState.RUNNING,
                "rds-operation",
                7002,
                local_port=13306,
            ),
            None,
        )
        self.active = [result]
        return OperationResult("start-operation", OperationState.SUCCEEDED, value=result)

    def resume(self, operation_id, code):
        raise AssertionError("MFA is not expected in this fake")

    def active_tunnels(self, profile_id=None):
        return self.active

    def stop(self, operation_id):
        self.stop_calls.append(operation_id)
        self.active = []
        return OperationResult(
            operation_id,
            OperationState.SUCCEEDED,
            TunnelHandle("session-rds", TunnelOwner.GUI, OperationState.CANCELLED),
        )

    def stop_all(self):
        self.stop_all_calls += 1
        self.active = []
        return []


def test_ec2_page_filters_selects_and_uses_external_application_operation() -> None:
    _app()
    ec2 = FakeEc2()
    page = Ec2Page(ec2, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    assert page.table.rowCount() == 2
    page.region.setCurrentText("us-east-1")
    page.region.lineEdit().editingFinished.emit()

    page.filter.setText("10.0.0.20")
    assert page.table.rowCount() == 1
    page.table.selectRow(0)
    page.open_selected()

    assert page.region.currentText() == "us-east-1"
    assert page.status.currentData() == "all"
    assert ec2.list_calls[-1] == (1, "us-east-1")
    assert ec2.connect_calls[0] == (
        "i-abcdef01234567890",
        1,
        "us-east-1",
    )
    assert "선택한 인스턴스: i-abcdef01234567890 · worker" in page.session_state.text()


def test_ec2_table_separates_power_ssm_and_actions() -> None:
    app = _app()
    app.setStyleSheet(APP_STYLE)
    ec2 = FakeEc2()
    ec2.targets = [
        Ec2Target(
            "i-0123456789abcdef0",
            "web-dev",
            "10.0.0.10",
            "Linux",
            "Online",
            "running",
            True,
        ),
        Ec2Target(
            "i-abcdef01234567890",
            "worker",
            "10.0.0.20",
            "Linux",
            "Offline",
            "running",
        ),
        Ec2Target(
            "i-11111111111111111",
            "admin",
            "10.0.0.30",
            "Linux",
            "Offline",
            "stopped",
        ),
    ]
    page = Ec2Page(ec2, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)

    assert page.table.columnCount() == 8
    assert [page.table.horizontalHeaderItem(i).text() for i in range(8)] == [
        "★",
        "이름",
        "Instance ID",
        "Private IP",
        "EC2 상태",
        "SSM",
        "최근 접속",
        "작업",
    ]
    assert page.region.isEditable()
    assert page.filter.placeholderText() == "이름, Instance ID, Private IP로 검색"
    assert not page.filter.actions()[0].icon().isNull()
    assert page.refresh_button.property("action_button") is True
    assert not page.table.cellWidget(0, 0).findChild(QPushButton).icon().isNull()
    action = page.table.cellWidget(0, 7).findChild(QPushButton, "ec2_row_action")
    assert action.toolTip() == "터미널 열기"
    assert action.isEnabled()
    stopped = page.table.cellWidget(1, 7).findChild(QPushButton, "ec2_row_action")
    assert stopped.toolTip() == "인스턴스 실행"
    offline = page.table.cellWidget(2, 7).findChild(QPushButton, "ec2_row_action")
    assert not offline.isEnabled()
    assert page.table.cellWidget(0, 5).findChild(QLabel, "icon_text_label").text() == "준비됨"
    assert page.table.cellWidget(2, 5).findChild(QLabel, "icon_text_label").text() == "지원 안됨"
    assert page.table.rowHeight(0) == 58
    assert page.table.horizontalHeader().sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch
    page.resize(1308, 860)
    page.show()
    app.processEvents()
    assert page.table.horizontalScrollBar().maximum() == 0
    assert action.width() == action.height() == 34
    offline.click()
    assert ec2.power_calls == []
    stopped.click()
    assert ec2.power_calls == [("start", "i-11111111111111111", 1, "ap-northeast-2")]
    page.close()


def test_ec2_error_recovers_without_closing_page() -> None:
    _app()
    ec2 = FakeEc2()
    ec2.fail = True
    errors: list[ApplicationError] = []
    page = Ec2Page(ec2, ImmediateRunner())  # type: ignore[arg-type]
    page.error_raised.connect(errors.append)

    page.set_profile(1)

    assert errors[0].message_code == "profile.not_found"
    assert page.refresh_button.isEnabled()
    assert not page.isHidden() or not page.isVisible()


def test_ec2_timer_reaps_all_profiles_but_displays_only_current_profile() -> None:
    _app()
    ec2 = FakeEc2()
    prior_failure = PluginExecutionError("plugin.exit.nonzero", "exit 9")
    ec2.reaped = [
        ExternalSessionHandle(
            "prior",
            7001,
            "session-prior",
            "i-prior",
            1,
            OperationState.FAILED,
            9,
            prior_failure,
        ),
        ExternalSessionHandle(
            "current",
            7002,
            "session-current",
            "i-current",
            2,
            OperationState.RUNNING,
        ),
    ]
    errors: list[ApplicationError] = []
    page = Ec2Page(ec2, ImmediateRunner())  # type: ignore[arg-type]
    page.error_raised.connect(errors.append)
    page.set_profile(2)

    page.refresh_session_states()

    assert ec2.reap_calls == 1
    assert page.session_state.text() == "인스턴스를 선택하세요."
    assert errors == []


def test_rds_crud_start_stop_and_dashboard_projection_share_application_dtos() -> None:
    app = _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    tunnels = FakeTunnels(saved)
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        tunnels,  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: "123456",
        lambda _parent, _id: True,
    )
    summaries: list[object] = []
    page.tunnels_changed.connect(lambda value: summaries.append(value))
    page.set_profile(1)
    page.session_list.setCurrentRow(0)
    assert page.connection_button.text() == ""
    assert page.connection_button.toolTip() == "연결 시작"
    assert page.connection_button.accessibleName() == "연결 시작"
    assert page.start_button is page.stop_button is page.connection_button
    page.connection_button.click()

    assert tunnels.start_calls == [StartTunnelRequest(3, 1, "i-0123456789abcdef0")]
    active_row = page.active_list.itemWidget(page.active_list.item(0))
    assert active_row is not None
    assert "개발 DB" in active_row.summary.display_text  # type: ignore[attr-defined]
    assert summaries[-1][0].local_port == 13306  # type: ignore[index,union-attr]
    assert "127.0.0.1:13306" in page.tunnel_state.text()
    assert page.copy_address_button.isEnabled()
    assert page.connection_button.text() == ""
    assert page.connection_button.toolTip() == "연결 종료"
    assert (
        page.session_list.itemWidget(page.session_list.item(0)).findChild(QFrame, "status_dot")
        is not None
    )
    assert page.connection_button.accessibleName() == "연결 종료"
    page.copy_address_button.click()
    assert app.clipboard().text() == "Host: 127.0.0.1\nPort: 13306"
    page.copy_address_button.menu().actions()[2].trigger()
    assert app.clipboard().text() == "127.0.0.1:13306"

    page.connection_button.click()
    assert tunnels.stop_calls == ["rds-operation"]
    assert page.active_list.count() == 0
    assert page.connection_button.text() == ""
    assert page.connection_button.toolTip() == "연결 시작"
    assert page.connection_button.accessibleName() == "연결 시작"


def test_rds_complete_new_session_saves_fixed_relay_request() -> None:
    _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        FakeTunnels(saved),  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )
    page.set_profile(1)

    page.name.setText("신규 DB")
    page.host.setText("new-db.example.internal")
    page.remote_port.setValue(5432)
    page.local_port.setValue(15432)
    assert page.save_button.isEnabled()
    page.save_button.click()

    assert saved.saved == [
        SaveTunnelSessionRequest(
            1,
            "신규 DB",
            "new-db.example.internal",
            5432,
            15432,
            TargetMode.FIXED,
            "i-0123456789abcdef0",
        )
    ]


def test_rds_active_poll_preserves_unsaved_local_port_and_selection() -> None:
    _app()
    saved = FakeSavedSessions()
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        FakeTunnels(saved),  # type: ignore[arg-type]
        FakeEc2(),  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )
    page.set_profile(1)
    page.session_list.setCurrentRow(0)
    page.local_port.setValue(15432)

    page._active_loaded([])

    assert page._selected_id == 3
    assert page.session_list.currentRow() == 0
    assert page.local_port.value() == 15432


def test_rds_connection_saves_current_editor_values_before_starting() -> None:
    _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    tunnels = FakeTunnels(saved)
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        tunnels,  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )
    page.set_profile(1)
    page.session_list.setCurrentRow(0)
    page.host.setText("edited.example.internal")
    page.local_port.setValue(15432)

    # Changing the port disables connection until the debounced check completes.
    assert not page.connection_button.isEnabled()
    assert QSignalSpy(page._port_timer.timeout).wait(2000)
    assert page.connection_button.isEnabled()
    page.connection_button.click()

    assert saved.saved[-1].host == "edited.example.internal"
    assert saved.saved[-1].local_port == 15432
    assert tunnels.start_calls == [StartTunnelRequest(3, 1, None)]


def test_rds_connection_does_not_start_when_current_values_fail_to_save() -> None:
    _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    saved.update = Mock(  # type: ignore[method-assign]
        side_effect=ConfigurationError("tunnel.save_failed", "저장 실패")
    )
    tunnels = FakeTunnels(saved)
    errors: list[ApplicationError] = []
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        tunnels,  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )
    page.error_raised.connect(errors.append)
    page.set_profile(1)
    page.session_list.setCurrentRow(0)
    page.host.setText("edited.example.internal")

    page.connection_button.click()

    assert not tunnels.start_calls
    assert errors[0].message_code == "tunnel.save_failed"


def test_rds_mockup_split_cards_and_actions_are_single_row() -> None:
    app = _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        FakeTunnels(saved),  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )
    page.set_profile(1)
    page.resize(1080, 720)
    page.show()
    app.processEvents()

    assert page.session_card.width() == 310
    assert page.session_card.objectName() == "session_list_card"
    assert page.editor_card.objectName() == "editor_card"
    assert page.findChildren(QPushButton).count(page.connection_button) == 1
    assert page.save_button.geometry().top() == page.connection_button.geometry().top()
    assert page.delete_button.isHidden()
    assert page.clone_button.isHidden()
    more = page.session_list.findChild(QPushButton, "rds_session_more")
    assert more is not None
    more.click()
    menu = more.findChild(QMenu, "rds_session_menu")
    assert menu is not None
    assert [action.text() for action in menu.actions()] == [
        "터널 이름 변경",
        "세션 복제",
        "세션 삭제",
    ]
    assert all(action.isEnabled() for action in menu.actions())
    menu.close()
    assert page.active_list.isHidden()
    assert not hasattr(page, "target_mode")
    assert page.host.width() == page.name.width()

    page.resize(760, 720)
    app.processEvents()

    assert page.session_card.width() == 230
    assert page.save_button.geometry().top() == page.connection_button.geometry().top()


def test_rds_relay_unavailable_disables_save_with_reason() -> None:
    _app()
    ec2 = FakeEc2()
    ec2.targets = []
    saved = FakeSavedSessions()
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        FakeTunnels(saved),  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )

    assert not page.save_button.isEnabled()
    assert "불러오기 전" in page.relay_status.text()
    page.set_profile(1)

    assert not page.save_button.isEnabled()
    assert not page.relay.isEnabled()
    assert "연결 가능한 중계 EC2가 없어" in page.relay_status.text()


def test_rds_connection_action_blocks_duplicate_start_while_pending() -> None:
    _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    tunnels = FakeTunnels(saved)
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        tunnels,  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )
    page.set_profile(1)
    page.session_list.setCurrentRow(0)
    queued = QueuedRunner()
    page._runner = queued  # type: ignore[assignment]

    page.toggle_connection()
    page.toggle_connection()

    assert len(queued.pending) == 1
    assert tunnels.start_calls == []
    assert not page.connection_button.isEnabled()


def test_rds_new_session_confirms_dirty_editor_before_reset() -> None:
    _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    confirmations = iter((False, True))
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        FakeTunnels(saved),  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
        discard_confirmation=lambda _parent: next(confirmations),
    )
    page.set_profile(1)
    page.session_list.setCurrentRow(0)
    page.host.setText("edited.example.internal")

    page.new_session()
    assert page._selected_id == 3
    assert page.host.text() == "edited.example.internal"

    page.new_session()
    assert page._selected_id is None
    assert page.editor_title.text() == "새 세션"
    assert page.name.text() == ""
    assert page.host.text() == ""
    assert page.remote_port.value() == 3306
    assert page.local_port.value() == 13306
    assert page.delete_button.isEnabled()
    assert not page.clone_button.isEnabled()
    assert not page.connection_button.isEnabled()


def test_rds_profile_change_refreshes_saved_sessions_and_shutdown_stops_owned_tunnels() -> None:
    _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    tunnels = FakeTunnels(saved)
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        tunnels,  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )
    completed: list[bool] = []

    page.set_profile(1)
    page.set_profile(2)
    page.shutdown(lambda: completed.append(True))

    assert page.session_list.count() == 1
    assert tunnels.stop_all_calls == 1
    assert completed == [True]


def test_rds_clone_confirms_new_name_and_never_updates_original() -> None:
    _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    tunnels = FakeTunnels(saved)
    notices: list[str] = []
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        tunnels,  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
        lambda _parent, source_name: (f"{source_name} 복사본", True),
    )
    page.notice_raised.connect(notices.append)
    page.set_profile(1)
    page.session_list.setCurrentRow(0)

    page.clone_button.click()

    assert saved.cloned == [(3, "개발 DB 복사본", 1)]
    assert saved.saved == []
    assert page._selected_id is None
    assert notices == ["RDS 터널 세션을 복제했습니다."]


def test_rds_clone_dialog_cancel_has_no_side_effect() -> None:
    _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        FakeTunnels(saved),  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
        lambda _parent, source_name: (f"{source_name} 복사본", False),
    )
    page.set_profile(1)
    page.session_list.setCurrentRow(0)

    page.clone_button.click()

    assert saved.cloned == []


def test_main_window_close_cleans_gui_tunnels_and_feature_pages_fit_1024x720() -> None:
    app = _app()
    ec2 = FakeEc2()
    saved = FakeSavedSessions()
    tunnels = FakeTunnels(saved)
    window = MainWindow(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        ec2,  # type: ignore[arg-type]
        saved,  # type: ignore[arg-type]
        tunnels,  # type: ignore[arg-type]
        connection_lifecycle=Mock(),
        task_runner=ImmediateRunner(),  # type: ignore[arg-type]
        auto_start=False,
    )
    window.resize(1024, 720)
    window.show()
    app.processEvents()

    assert window.ec2_page is not None and window.rds_page is not None
    for page in (window.ec2_page, window.rds_page):
        window.pages.setCurrentWidget(page)
        app.processEvents()
        assert page.width() > 0 and page.height() > 0
        assert window.pages.rect().contains(page.geometry())
        critical = (
            (page.region, page.status, page.filter, page.table)
            if isinstance(page, Ec2Page)
            else (
                page.name,
                page.host,
                page.copy_address_button,
                page.start_button,
                page.stop_button,
            )
        )
        for widget in critical:
            assert widget.width() > 0 and widget.height() > 0
            assert page.rect().contains(widget.mapTo(page, widget.rect().center()))

    window.rds_page.set_profile(1)
    window.rds_page.session_list.setCurrentRow(0)
    window.rds_page.start()
    assert window.findChild(QWidget, "dashboard_active_tunnels") is None
    window.rds_page.stop_operation("rds-operation")
    assert tunnels.stop_calls == ["rds-operation"]

    window.close()
    app.processEvents()

    assert tunnels.stop_all_calls == 1
    assert window.isHidden()


def test_rds_endpoint_catalog_selects_host_and_port_without_expanding_editor() -> None:
    _app()
    saved = FakeSavedSessions()
    endpoints = Mock()
    endpoints.list.return_value = [
        RdsEndpoint(
            "orders-with-a-very-long-production-identifier",
            "orders-with-a-very-long-database-hostname.cluster.internal",
            5432,
            "postgres",
        )
    ]
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        FakeTunnels(saved),  # type: ignore[arg-type]
        FakeEc2(),  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
        endpoints=endpoints,
    )

    page.set_profile(1)

    assert not page.host_catalog.isHidden()
    page.resize(900, 720)
    page.show()
    _app().processEvents()

    assert page.host.text() == "orders-with-a-very-long-database-hostname.cluster.internal"
    assert page.remote_port.value() == 5432
    assert page.host_catalog.width() <= page.editor_card.contentsRect().width()
    assert page.notice.height() >= page.notice.minimumSizeHint().height()
    assert page.notice.width() <= page.editor_card.contentsRect().width()
    endpoints.list.assert_called_once_with(1)


def test_rds_draft_survives_polling_and_save_switches_to_update() -> None:
    _app()
    saved = FakeSavedSessions()
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        FakeTunnels(saved),  # type: ignore[arg-type]
        FakeEc2(),  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )
    page.set_profile(1)
    page.new_button.click()
    assert page.session_list.count() == 2
    assert saved.saved == []
    page.name.setText("새 DB")
    page.host.setText("new.example.internal")
    page.refresh_active()
    page.reload()
    assert page.session_list.count() == 2
    assert page.name.text() == "새 DB"
    assert page.host.text() == "new.example.internal"
    assert not page.connection_button.isEnabled()
    page.save()
    assert saved.saved[0].tunnel_id is None
    assert page.session_list.count() == 1
    assert page._selected_id == saved.item.id
    page.save()
    assert saved.saved[1].tunnel_id == saved.item.id


def test_rds_deleting_empty_draft_does_not_delete_saved_session() -> None:
    _app()
    saved = FakeSavedSessions()
    page = RdsPage(
        saved,  # type: ignore[arg-type]
        FakeTunnels(saved),  # type: ignore[arg-type]
        FakeEc2(),  # type: ignore[arg-type]
        ImmediateRunner(),  # type: ignore[arg-type]
        lambda _parent, _arn: None,
        lambda _parent, _id: True,
    )
    page.set_profile(1)
    page.new_button.click()
    page.new_button.click()
    assert page.session_list.count() == 2
    page.delete_button.click()
    assert saved.deleted == []
    assert page.session_list.count() == 1
    assert page._selected_id == saved.item.id


def test_ec2_favorites_selection_and_history_combine_without_cross_profile_state() -> None:
    from dataclasses import replace
    from datetime import datetime

    _app()
    service = FakeEc2()
    timestamp = datetime.now().astimezone().replace(hour=10, minute=24)
    service.targets[0] = replace(service.targets[0], favorite=True, last_connected_at=timestamp)
    page = Ec2Page(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page.table.selectRow(0)
    assert f"마지막 연결 성공 ({timestamp:%Y-%m-%d %H:%M})" in page.session_state.text()
    page.favorites_only.setChecked(True)
    assert page.table.rowCount() == 1
    assert "web-dev" in page.session_state.text()
    page.filter.setText("10.0.0.20")
    assert page.table.rowCount() == 0
    assert page.session_state.text() == "인스턴스를 선택하세요."
    page.filter.clear()
    page.status.setCurrentIndex(2)
    assert page.table.rowCount() == 0
    page.status.setCurrentIndex(0)
    page.table.selectRow(0)
    service.targets[0] = replace(service.targets[0], favorite=False)
    page.reload()
    assert page.table.rowCount() == 0
    page.favorites_only.setChecked(False)
    page.table.selectRow(0)
    page.set_profile(None)
    assert page.session_state.text() == "인스턴스를 선택하세요."
    assert page.table.rowCount() == 0


def test_ec2_more_menu_copies_through_policy_and_opens_readonly_tags(monkeypatch) -> None:
    from dataclasses import replace

    from PySide6.QtWidgets import QDialog, QMenu, QTableWidget

    from aws_connect.presentation.gui import ec2_rds

    app = _app()
    copied: list[str] = []
    monkeypatch.setattr(ec2_rds, "copy_temporarily", copied.append)
    service = FakeEc2()
    service.targets[0] = replace(service.targets[0], tags=(("Environment", "test"),))
    page = Ec2Page(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page.show()
    more = page.table.cellWidget(0, 7).findChild(QPushButton, "ec2_more")
    more.click()
    menu = more.findChild(QMenu)
    assert [action.text() for action in menu.actions()] == [
        "인스턴스 ID 복사",
        "Private IP 복사",
        "태그 보기",
    ]
    menu.actions()[0].trigger()
    menu.actions()[1].trigger()
    assert copied == [service.targets[0].instance_id, "10.0.0.10"]
    menu.actions()[2].trigger()
    app.processEvents()
    dialog = page.findChild(QDialog, "ec2_tags_dialog")
    assert dialog.isVisible() and not dialog.isModal()
    table = dialog.findChild(QTableWidget)
    assert table.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers
    assert table.item(0, 0).text() == "Environment"
    assert table.item(0, 1).text() == "test"
    dialog.close()
    page.close()


def test_ec2_stale_inventory_result_is_ignored_after_profile_switch() -> None:
    _app()
    service = FakeEc2()
    runner = QueuedRunner()
    page = Ec2Page(service, runner)  # type: ignore[arg-type]
    page.set_profile(1)
    page.set_profile(2)
    runner.pending[1][1]([service.targets[1]])
    runner.pending[0][1]([service.targets[0]])
    assert page.table.item(0, 1).text() == "worker"


def test_ec2_connection_time_uses_calendar_day_and_blank_history() -> None:
    from datetime import datetime, timedelta

    from aws_connect.presentation.gui.view_models import ec2_connection_time

    now = datetime.now().astimezone().replace(hour=10, minute=24)
    assert ec2_connection_time(None, now=now) == "-"
    assert ec2_connection_time(now, now=now) == now.strftime("%Y-%m-%d %H:%M")
    assert ec2_connection_time(now - timedelta(days=1), now=now) == (
        now - timedelta(days=1)
    ).strftime("%Y-%m-%d %H:%M")
    assert ec2_connection_time(now - timedelta(days=2), now=now) == (
        now - timedelta(days=2)
    ).strftime("%Y-%m-%d %H:%M")


def test_ec2_start_keeps_filtered_row_until_running_and_ssm_ready():
    from dataclasses import replace

    _app()
    service = FakeEc2()
    target = replace(
        service.targets[0], instance_state="stopped", ssm_ping_status="Offline", favorite=True
    )
    service.targets = [target]
    page = Ec2Page(service, ImmediateRunner())
    page.set_profile(1)
    page.status.setCurrentIndex(page.status.findData("stopped"))
    page.filter.setText("web-dev")
    page.favorites_only.setChecked(True)
    page.run_target_action(target)
    assert page._starting_targets[target.instance_id].instance_state == "pending"
    assert page.table.rowCount() == 1
    service.targets = []  # EC2 eventual consistency must not remove the initiated row.
    page._refresh_starting_instances()
    assert page.table.rowCount() == 1
    service.targets = [replace(target, instance_state="running")]
    page._refresh_starting_instances()
    assert page.table.rowCount() == 1 and page._instance_timer.isActive()
    service.targets = [replace(target, instance_state="running", ssm_ping_status="Online")]
    page._refresh_starting_instances()
    assert page.table.rowCount() == 1 and not page._instance_timer.isActive()
    assert page._target_by_id[target.instance_id].ssm_ready
    assert page.status.currentData() == "stopped"
    assert page.filter.text() == "web-dev" and page.favorites_only.isChecked()
    page.set_profile(2)
    assert not page._starting_targets and not page._pending_until
    page.close()


def test_ec2_start_completion_cannot_leak_to_another_profile():
    from dataclasses import replace

    _app()
    service, runner = FakeEc2(), QueuedRunner()
    page = Ec2Page(service, runner)
    page.set_profile(1)
    target = replace(service.targets[0], instance_state="stopped")
    page._run_power_action(target, "start")
    callback = runner.pending[-1][1]
    page.set_profile(2)
    callback(object())
    assert not page._starting_targets and not page._instance_timer.isActive()
    page.close()
