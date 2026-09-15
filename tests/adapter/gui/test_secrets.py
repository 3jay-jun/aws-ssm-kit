from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMessageBox, QPushButton

from aws_connect.application.ports import ListedSecret
from aws_connect.application.secrets_service import (
    SecretField,
    SecretKind,
    SecretLookupMode,
    SecretRelayTarget,
    SecretResult,
)
from aws_connect.domain.errors import ApplicationError, AwsPermissionError
from aws_connect.domain.saved_secret import SavedSecret
from aws_connect.presentation.gui.errors import map_error
from aws_connect.presentation.gui.secrets import SecretsPage
from aws_connect.presentation.gui.window import MainWindow

RAW = "phase-seven-private-value"  # pragma: allowlist secret


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


class FakeSecrets:
    def __init__(self) -> None:
        self.result = SecretResult(
            "arn:test",
            SecretKind.JSON,
            (
                SecretField("host", "db.internal", False),
                SecretField("port", 3306, False),
                SecretField("password", RAW, True),
            ),
            _raw_secret_string=(
                '{"host":"db.internal","port":3306,"password":"phase-seven-private-value"}'  # noqa: E501  # pragma: allowlist secret
            ),
        )
        self.list_error: ApplicationError | None = None
        self.saved: list[SavedSecret] = []

    def get(
        self,
        identifier: str,
        profile: int | None = None,
        *,
        mode: SecretLookupMode = SecretLookupMode.DIRECT,
        instance_id: str | None = None,
        confirmed: bool = False,
    ) -> SecretResult:
        assert identifier
        assert profile == 1
        self.last_lookup = (mode, instance_id, confirmed)
        self.remember(self.result.secret_id, profile)
        return self.result

    def list(self, profile: int | None = None) -> list[ListedSecret]:
        assert profile is not None
        if self.list_error is not None:
            raise self.list_error
        return [ListedSecret("db/dev", "arn:test")]

    def list_relay_targets(self, profile: int | None = None) -> list[SecretRelayTarget]:
        assert profile is not None
        return [SecretRelayTarget("i-online", "Amazon Linux", "web-dev")]

    def list_saved(self, profile: int | None = None) -> list[SavedSecret]:
        assert profile is not None
        return list(self.saved)

    def remember(self, identifier: str, profile: int | None = None) -> SavedSecret:
        assert profile is not None
        existing = next((item for item in self.saved if item.identifier == identifier), None)
        if existing is not None:
            return existing
        created = SavedSecret(len(self.saved) + 1, profile, identifier)
        self.saved.append(created)
        return created

    def update_saved(
        self, saved_id: int, identifier: str, profile: int | None = None
    ) -> SavedSecret:
        assert profile is not None
        updated = SavedSecret(saved_id, profile, identifier)
        self.saved = [updated if item.id == saved_id else item for item in self.saved]
        return updated

    def delete_saved(self, saved_id: int, profile: int | None = None) -> None:
        assert profile is not None
        self.saved = [item for item in self.saved if item.id != saved_id]


def test_direct_lookup_masks_then_explicitly_reveals_and_clears_on_profile_change() -> None:
    _app()
    service = FakeSecrets()
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page.get_secret()

    assert page.fields.item(2, 1).text() == "***REDACTED***"
    assert RAW not in " ".join(
        page.fields.item(row, 1).text() for row in range(page.fields.rowCount())
    )
    page.fields.selectRow(2)
    page.reveal_selected()
    assert page.fields.item(2, 1).text() == RAW
    assert page.catalog.count() == 1

    page.set_profile(2)
    assert page.fields.rowCount() == 0
    assert page._result is None


def test_secrets_page_matches_mockup_header_toolbar_and_360px_split() -> None:
    _app()
    page = SecretsPage(FakeSecrets(), ImmediateRunner())  # type: ignore[arg-type]

    assert page.findChild(QLabel, "page_title").text() == "Secrets Manager"
    assert page.findChild(QFrame, "secret_toolbar") is not None
    catalog = page.findChild(QFrame, "secret_catalog_card")
    assert catalog is not None
    assert catalog.minimumWidth() == 360
    assert catalog.maximumWidth() == 360
    assert page.findChild(QFrame, "secret_result_card") is not None
    assert page.list_button.text() == "↻ 새로고침"


def test_sensitive_value_reveal_is_temporary_without_copy_or_aws_save_buttons(monkeypatch) -> None:
    _app()
    page = SecretsPage(FakeSecrets(), ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page.get_secret()
    page.fields.selectRow(2)
    callbacks: list[Callable[[], None]] = []
    monkeypatch.setattr(
        "aws_connect.presentation.gui.secrets.QTimer.singleShot",
        lambda _milliseconds, callback: callbacks.append(callback),
    )

    page.reveal_selected()

    assert page.fields.item(2, 1).text() == RAW
    assert page.findChild(QPushButton, "secret_copy") is None
    assert page.findChild(QPushButton, "secret_save_field") is None
    callbacks[0]()
    assert page.fields.item(2, 1).text() == "***REDACTED***"


def test_optional_list_permission_failure_keeps_direct_input_available() -> None:
    _app()
    service = FakeSecrets()
    service.list_error = AwsPermissionError(
        "aws.permission.denied",
        "AccessDeniedException",
        aws_service="secretsmanager",
        aws_action="ListSecrets",
    )
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    errors: list[ApplicationError] = []
    notices: list[str] = []
    page.error_raised.connect(errors.append)
    page.notice_raised.connect(notices.append)
    page.set_profile(1)

    page.load_list()

    assert errors == []
    assert "직접 검색" in notices[-1]
    assert "목록 조회 권한이 없습니다" in page.catalog_status.text()
    assert page.secret_selector.isHidden()
    assert not page.secret_id.isHidden()
    page.secret_id.setText("db/dev")
    assert page.get_button.isEnabled()
    page.get_secret()
    assert page.fields.rowCount() == 3
    mapped = map_error(service.list_error)
    assert "secretsmanager:ListSecrets" in mapped.message


def test_send_command_permission_error_explains_terminal_fallback() -> None:
    error = AwsPermissionError(
        "aws.permission.denied",
        "AccessDeniedException",
        aws_service="ssm",
        aws_action="SendCommand",
    )

    mapped = map_error(error)

    assert mapped.title == "SSM Run Command 권한이 없습니다"
    assert "EC2 접속" in mapped.message
    assert "aws secretsmanager get-secret-value" in mapped.message


def test_start_session_permission_error_explains_terminal_requirement() -> None:
    error = AwsPermissionError(
        "aws.permission.denied",
        "AccessDeniedException",
        aws_service="ssm",
        aws_action="StartSession",
    )

    mapped = map_error(error)

    assert mapped.title == "EC2 터미널을 열 권한이 없습니다"
    assert "SSM Online" in mapped.message
    assert "ssm:StartSession" in mapped.message


def test_send_command_denial_auto_runs_fixed_lookup_and_keeps_terminal_open() -> None:
    _app()
    service = FakeSecrets()
    service.list_error = AwsPermissionError(
        "aws.permission.denied",
        "AccessDeniedException",
        aws_service="secretsmanager",
        aws_action="ListSecrets",
    )
    ec2 = Mock()
    page = SecretsPage(
        service,
        ImmediateRunner(),
        ec2=ec2,  # type: ignore[arg-type]
    )
    page.set_profile(1)
    page.secret_id.setText("db/dev")
    page.lookup_mode.setCurrentIndex(1)
    page.load_relays()
    error = AwsPermissionError(
        "aws.permission.denied",
        "AccessDeniedException",
        aws_service="ssm",
        aws_action="SendCommand",
    )

    page._failed(error)
    page.terminal_fallback_button.click()

    assert not page.terminal_fallback_button.isHidden()
    ec2.connect_external.assert_called_once_with(
        "i-online",
        1,
        document_name="AWS-StartInteractiveCommand",
        parameters={
            "command": [
                "aws secretsmanager get-secret-value --secret-id 'db/dev' "
                '--query SecretString --output text; exec "${SHELL:-/bin/sh}" -l'
            ]
        },
    )
    assert page.terminal_fallback_button.text() == "EC2 직접 조회"


def test_available_catalog_uses_select_box_and_selected_arn_for_direct_get() -> None:
    _app()
    page = SecretsPage(FakeSecrets(), ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page.get_button.click()

    assert page.secret_id.text() == "arn:test"
    assert not page.secret_selector.isHidden()
    assert page.secret_id.isHidden()
    assert page.fields.rowCount() == 3
    assert page.fields.currentRow() == 0
    assert page.findChild(QPushButton, "secret_relay_refresh") is None


def test_via_ec2_requires_online_selection_and_one_time_explicit_consent() -> None:
    _app()
    service = FakeSecrets()
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page.secret_id.setText("db/dev")
    page.lookup_mode.setCurrentIndex(1)

    assert not page.relay_warning.isHidden()
    assert not page.get_button.isEnabled()
    page.load_relays()
    assert page.relay_instance.count() == 1
    assert "web-dev · i-online" in page.relay_instance.currentText()
    assert not page.get_button.isEnabled()
    page.relay_consent.setChecked(True)
    assert page.get_button.isEnabled()

    page.get_secret()

    assert service.last_lookup == (SecretLookupMode.VIA_EC2, "i-online", True)
    assert not page.relay_consent.isChecked()
    assert not page.get_button.isEnabled()


def test_saved_secret_buttons_register_edit_and_delete_only_sqlite_reference(monkeypatch) -> None:
    _app()
    service = FakeSecrets()
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page.get_secret()
    assert page.catalog.item(0).text() == "arn:test"
    page.catalog.setCurrentRow(0)
    monkeypatch.setattr(
        "aws_connect.presentation.gui.secrets.QInputDialog.getText",
        lambda *_args, **_kwargs: ("db/prod", True),
    )
    monkeypatch.setattr(
        "aws_connect.presentation.gui.secrets.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    page.edit_saved_button.click()
    assert service.saved[0].identifier == "db/prod"

    page.catalog.setCurrentRow(0)
    page.delete_saved_button.click()
    assert service.saved == []


def test_main_window_wires_secret_endpoint_to_unsaved_rds_editor() -> None:
    _app()
    profiles = Mock()
    authentication = Mock()
    operations = Mock()
    ec2 = Mock()
    saved = Mock()
    tunnels = Mock()
    secrets = FakeSecrets()
    window = MainWindow(
        profiles,
        authentication,
        operations,
        ec2,
        saved,
        tunnels,
        secrets,
        connection_lifecycle=Mock(),
        task_runner=ImmediateRunner(),  # type: ignore[arg-type]
        auto_start=False,
    )
    assert window.secrets_page is not None
    assert window.rds_page is not None
    window.secrets_page._result = secrets.result

    window.secrets_page.copy_to_rds()

    assert window.pages.currentWidget() is window.rds_page
    assert window.rds_page.host.text() == "db.internal"
    assert window.rds_page.remote_port.value() == 3306
    assert window.rds_page.name.text() == ""
