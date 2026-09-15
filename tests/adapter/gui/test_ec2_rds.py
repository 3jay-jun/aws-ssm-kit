from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

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
    page.region.setText("us-east-1")
    page.region.editingFinished.emit()

    page.filter.setText("10.0.0.20")
    assert page.table.rowCount() == 1
    page.table.selectRow(0)
    page.open_selected()

    assert page.region.text() == "us-east-1"
    assert page.status.currentData() == "all"
    assert ec2.list_calls[-1] == (1, "us-east-1")
    assert ec2.connect_calls[0] == (
        "i-abcdef01234567890",
        1,
        "us-east-1",
    )
    assert "PID 7001" in page.session_state.text()


def test_ec2_table_uses_single_power_status_column_and_svg_favorites() -> None:
    _app()
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

    assert page.table.columnCount() == 6
    assert [page.status.itemText(index) for index in range(page.status.count())] == [
        "전체",
        "실행 중",
        "중지됨",
    ]
    assert page.table.horizontalHeaderItem(4).text() == "EC2 상태"
    assert all(
        page.table.horizontalHeaderItem(column).text() != "SSM 상태"
        for column in range(page.table.columnCount())
    )
    assert page.table.cellWidget(0, 0).icon().isNull() is False  # type: ignore[union-attr]
    assert page.table.cellWidget(0, 5).text() == "터미널 열기 ↗"  # type: ignore[union-attr]
    assert page.table.cellWidget(1, 5).text() == "시작"  # type: ignore[union-attr]
    assert page.table.cellWidget(2, 5).text() == "재부팅"  # type: ignore[union-attr]
    stopped_status = page.table.cellWidget(1, 4).findChild(QLabel)  # type: ignore[union-attr]
    assert stopped_status.text() == "● 중지됨"
    assert stopped_status.property("status") == "danger"
    assert page.table.rowHeight(0) == 54
    assert page.table.columnWidth(1) == 330
    assert page.table.columnWidth(5) == 92
    assert page.table.cellWidget(0, 5).width() == 84  # type: ignore[union-attr]
    assert page.table_card.objectName() == "content_card"

    page.table.cellWidget(2, 5).click()  # type: ignore[union-attr]
    assert ec2.power_calls == [("reboot", "i-abcdef01234567890", 1, "ap-northeast-2")]


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
    assert page.session_state.text() == "i-current (PID 7002)"
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
    assert page.connection_button.text() == "연결 시작"
    assert page.start_button is page.stop_button is page.connection_button
    page.connection_button.click()

    assert tunnels.start_calls == [StartTunnelRequest(3, 1, "i-0123456789abcdef0")]
    active_row = page.active_list.itemWidget(page.active_list.item(0))
    assert active_row is not None
    assert "개발 DB" in active_row.summary.display_text  # type: ignore[attr-defined]
    assert summaries[-1][0].local_port == 13306  # type: ignore[index,union-attr]
    assert "127.0.0.1:13306" in page.tunnel_state.text()
    assert page.copy_address_button.isEnabled()
    assert page.connection_button.text() == "연결 종료"
    page.copy_address_button.click()
    assert app.clipboard().text() == "127.0.0.1:13306"

    page.connection_button.click()
    assert tunnels.stop_calls == ["rds-operation"]
    assert page.active_list.count() == 0
    assert page.connection_button.text() == "연결 시작"


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
    assert page.active_list.isHidden()
    assert not hasattr(page, "target_mode")
    assert page.host.width() == page.name.width()


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
    assert not page.delete_button.isEnabled()
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
    assert window.dashboard_tunnels.count() == 1
    dashboard_row = window.dashboard_tunnels.itemWidget(window.dashboard_tunnels.item(0))
    assert dashboard_row is not None
    stop_button = next(
        button for button in dashboard_row.findChildren(QPushButton) if button.text() == "연결 종료"
    )
    stop_button.click()
    assert tunnels.stop_calls == ["rds-operation"]
    assert window.dashboard_tunnels.count() == 0

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
    assert page.notice.height() == 42
    endpoints.list.assert_called_once_with(1)
