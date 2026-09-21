from datetime import UTC, datetime
from unittest.mock import Mock

from PySide6.QtWidgets import QLabel, QPushButton
from tests.adapter.gui.test_logs import ImmediateTaskRunner, _app
from tests.unit.application.test_dashboard_service import build

from aws_connect.application.dashboard_service import PermissionState
from aws_connect.domain.execution_log import (
    ExecutionLevel,
    ExecutionLogEvent,
    ExecutionPhase,
    ExecutionResult,
)
from aws_connect.presentation.gui.dashboard import DashboardPage
from aws_connect.presentation.gui.styles import APP_STYLE


def test_dashboard_cards_actions_results_and_refresh_are_service_backed():
    app = _app()
    service, *_ = build()
    activity = Mock()
    activity.list_recent.return_value = [
        ExecutionLogEvent(
            datetime.now(UTC),
            ExecutionLevel.INFO,
            ExecutionResult.SUCCESS,
            ExecutionPhase.COMPLETED,
            "s3",
            "upload",
            "example.txt",
            "완료",
        )
    ] * 5
    page = DashboardPage(service, activity, ImmediateTaskRunner())
    page.setStyleSheet(APP_STYLE)
    page.resize(1204, 802)
    page.show()
    routes = []
    page.activated.connect(routes.append)
    page.set_profile(1)
    page.refresh_button.click()
    page.refresh_recent()
    app.processEvents()
    assert page.checked_at.text() != "마지막 확인: -"
    assert len({card.y() for card in page.cards.values()}) == 1
    assert page.recent.rowCount() == 5
    activity.list_recent.assert_called_once_with(5)
    assert page.recent.item(0, 4).text() == "example.txt"
    assert page.recent.cellWidget(0, 1).findChild(QLabel, "icon_text_label").text() == "성공"
    for route, card in page.cards.items():
        card.findChild(QPushButton, f"dashboard_{route}_button").click()
    page.findChild(QPushButton, "dashboard_logs_button").click()
    assert routes == ["ec2", "rds", "secrets", "s3", "logs"]
    assert page.cards["s3"]._permissions["put"][0].accessibleName() == PermissionState.UNKNOWN.value
    page.close()


def test_stale_permission_result_cannot_cross_profile_or_refresh_generation():
    _app()
    service, *_ = build()
    result = service.check_permissions(1)
    callbacks = []
    runner = Mock()
    runner.submit.side_effect = lambda operation, success, error: callbacks.append((success, error))
    page = DashboardPage(service, None, runner)
    page.set_profile(1)
    page.refresh_permissions()
    page.set_profile(2)
    callbacks[0][0](result)
    assert page.checked_at.text() == "마지막 확인: -"
    assert page.refresh_button.isEnabled()
    page.set_profile(None)
    assert not page.refresh_button.isEnabled()
    assert all(
        icon.accessibleName() == "unknown"
        for card in page.cards.values()
        for icon, _ in card._permissions.values()
    )
    page.close()


def test_return_to_dashboard_refreshes_actual_s3_success_and_labels_unknown():
    import json

    _app()
    service, logs, *_ = build()
    page = DashboardPage(service, None, ImmediateTaskRunner())
    page.set_profile(1)
    page.refresh()
    assert "확인 필요" in page.cards["s3"]._permissions["put"][1].text()
    logs.list.return_value = [
        ExecutionLogEvent(
            datetime.now(UTC),
            ExecutionLevel.INFO,
            ExecutionResult.SUCCESS,
            ExecutionPhase.COMPLETED,
            "s3",
            "upload",
            "file.txt",
            "완료",
            aws_service="s3",
            aws_action=action,
            metadata_json=json.dumps(
                {"profile_id": 1, "region": "example-region", "bucket": "example-bucket"}
            ),
        )
        for action in ("PutObject", "DeleteObject")
    ]
    page.refresh()
    for key in ("put", "delete"):
        icon, label = page.cards["s3"]._permissions[key]
        assert icon.accessibleName() == "allowed"
        assert label.text().endswith("· 가능")
    page.close()
