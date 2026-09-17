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
        self.get_count = 0
        self.load_saved_count = 0

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
        self.get_count += 1
        self.last_lookup = (mode, instance_id, confirmed)
        self.remember(
            self.result.secret_id,
            profile,
            value=self.result.raw_secret_string(),
            lookup_mode=mode,
            relay_instance_id=instance_id if mode is SecretLookupMode.VIA_EC2 else None,
        )
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

    def remember(
        self,
        identifier: str,
        profile: int | None = None,
        *,
        value: str | None = None,
        lookup_mode: SecretLookupMode | None = None,
        relay_instance_id: str | None = None,
    ) -> SavedSecret:
        assert profile is not None
        existing = next((item for item in self.saved if item.identifier == identifier), None)
        if existing is not None and value is None and lookup_mode is None:
            return existing
        created = SavedSecret(
            existing.id if existing is not None else len(self.saved) + 1,
            profile,
            identifier,
            value="" if value is None else value,
            lookup_mode=lookup_mode or SecretLookupMode.DIRECT,
            relay_instance_id=relay_instance_id,
        )
        self.saved = [item for item in self.saved if item.id != created.id]
        self.saved.append(created)
        return created

    def update_saved(
        self,
        saved_id: int,
        identifier: str,
        profile: int | None = None,
        *,
        value: str | None = None,
    ) -> SavedSecret:
        assert profile is not None
        existing = next(item for item in self.saved if item.id == saved_id)
        updated = SavedSecret(
            saved_id,
            profile,
            identifier,
            value=existing.value if value is None else value,
            lookup_mode=existing.lookup_mode,
            relay_instance_id=existing.relay_instance_id,
        )
        self.saved = [updated if item.id == saved_id else item for item in self.saved]
        return updated

    def load_saved(
        self, saved_id: int, profile: int | None = None
    ) -> tuple[SavedSecret, SecretResult]:
        assert profile is not None
        self.load_saved_count += 1
        saved = next(item for item in self.saved if item.id == saved_id)
        return saved, SecretResult(
            saved.identifier,
            SecretKind.TEXT,
            (SecretField("value", saved.value, True),),
            _raw_secret_string=saved.value,
        )

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
    assert page.catalog.currentRow() == 0

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
    assert page.list_button.text() == ""
    assert page.list_button.toolTip() == "목록 새로고침"
    assert page.list_button.property("action_button") is True
    assert not page.list_button.icon().isNull()


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

    assert page.findChild(QLabel, "secret_relay_warning") is None
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


def test_saved_secret_buttons_edit_id_and_value_then_delete_only_sqlite_snapshot(
    monkeypatch,
) -> None:
    _app()
    service = FakeSecrets()
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page.get_secret()
    assert "arn:test" in page.catalog.item(0).toolTip()
    page.catalog.setCurrentRow(0)
    monkeypatch.setattr(
        "aws_connect.presentation.gui.secrets._prompt_saved_secret",
        lambda *_args, **_kwargs: ("db/prod", "locally-edited-value"),
    )
    monkeypatch.setattr(
        "aws_connect.presentation.gui.secrets.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    page.catalog.itemDoubleClicked.emit(page.catalog.item(0))
    assert service.saved[0].identifier == "db/prod"
    assert service.saved[0].value == "locally-edited-value"
    assert service.get_count == 1
    assert page.catalog.currentRow() == 0
    assert page._result is not None
    assert page._result.raw_secret_string() == "locally-edited-value"

    page.catalog.setCurrentRow(0)
    page.delete_saved_button.click()
    assert service.saved == []


def test_saved_click_restores_direct_snapshot_without_aws_lookup() -> None:
    _app()
    service = FakeSecrets()
    service.saved = [
        SavedSecret(
            7,
            1,
            "arn:test",
            value='{"password":"stored-value"}',  # pragma: allowlist secret
        )
    ]
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)

    page._catalog_selected(page.catalog.item(0))

    assert service.get_count == 0
    assert service.load_saved_count == 1
    assert page.lookup_mode.currentData() == SecretLookupMode.DIRECT
    assert not page.secret_selector.isHidden()
    assert page.secret_selector.currentData().arn == "arn:test"
    assert page.fields.item(0, 1).text() == "***REDACTED***"


def test_saved_click_restores_via_ec2_and_marks_missing_relay_unavailable() -> None:
    _app()
    service = FakeSecrets()
    service.saved = [
        SavedSecret(
            8,
            1,
            "db/via",
            value="stored-via-value",  # pragma: allowlist secret
            lookup_mode=SecretLookupMode.VIA_EC2,
            relay_instance_id="i-online",
        ),
        SavedSecret(
            9,
            1,
            "db/stale",
            value="stored-stale-value",  # pragma: allowlist secret
            lookup_mode=SecretLookupMode.VIA_EC2,
            relay_instance_id="i-missing",
        ),
    ]
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)

    page._catalog_selected(page.catalog.item(0))

    assert service.get_count == 0
    assert page.lookup_mode.currentData() == SecretLookupMode.VIA_EC2
    assert page.secret_id.text() == "db/via"
    assert not page.secret_id.isHidden()
    assert page.relay_instance.currentData().instance_id == "i-online"

    page.catalog.setCurrentRow(1)
    page._catalog_selected(page.catalog.item(1))

    assert service.get_count == 0
    assert page.relay_instance.currentData() is None
    assert page.relay_instance.currentText() == "사용 불가 · i-missing"
    assert not page.get_button.isEnabled()


def test_removed_secret_count_summary_and_relay_guidance_are_absent() -> None:
    _app()
    service = FakeSecrets()
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page.get_secret()

    visible_copy = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "조회 가능한 Secret" not in visible_copy
    assert "SQLite 저장 항목" not in visible_copy
    assert "Secret 값" not in visible_copy
    assert "경유 조회는 SSM Run Command" not in visible_copy


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


def test_register_dialog_cancel_and_save_without_aws(monkeypatch) -> None:
    _app()
    service = FakeSecrets()
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    monkeypatch.setattr(
        "aws_connect.presentation.gui.secrets._prompt_saved_secret", lambda *_: None
    )
    page.register_saved_button.click()
    assert not service.saved
    monkeypatch.setattr(
        "aws_connect.presentation.gui.secrets._prompt_saved_secret",
        lambda *_: ("local/example", "local-test-value"),
    )
    page.register_saved_button.click()
    assert service.saved[0].identifier == "local/example"
    assert service.saved[0].value == "local-test-value"
    assert service.get_count == 0
    assert page.catalog.currentRow() == 0


def test_copy_all_uses_exact_raw_value_and_shared_clipboard_policy(monkeypatch) -> None:
    _app()
    service = FakeSecrets()
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    copied: list[str] = []
    monkeypatch.setattr("aws_connect.presentation.gui.secrets.copy_temporarily", copied.append)
    page.copy_all()
    assert copied == []
    page.set_profile(1)
    page.get_secret()
    page.copy_all_button.click()
    assert copied == [service.result.raw_secret_string()]
    assert page.fields.item(2, 1).text() != RAW
    assert page.summary.isHidden()


def test_short_arn_keeps_full_lookup_identity_and_compact_rows() -> None:
    _app()
    service = FakeSecrets()
    identifier = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:demo/database-AbCdEf"
    service.saved = [SavedSecret(1, 1, identifier, value="local-test-value")]
    page = SecretsPage(service, ImmediateRunner())  # type: ignore[arg-type]
    page.set_profile(1)
    page._catalog_selected(page.catalog.item(0))
    assert page.secret_id.text() == "demo/database-AbCdEf"
    assert page._current_identifier() == identifier
    item = page.catalog.item(0)
    assert "arn:" not in item.toolTip()
    assert page.catalog.itemWidget(item).property("last_row") is True
    page.secret_id.setText("another/name")
    assert page._current_identifier() == "another/name"


def test_shared_secret_dialog_has_readonly_sequence_and_preserves_arn(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QPlainTextEdit

    from aws_connect.presentation.gui.secrets import _prompt_saved_secret

    _app()
    identifier = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:demo/db-AbCdEf"
    saved = SavedSecret(7, 1, identifier, value="local-test-value")

    def accept(dialog: QDialog) -> int:
        assert dialog.windowTitle() == "Secret 수정"
        sequence = dialog.findChild(QLineEdit, "saved_secret_sequence")
        assert sequence.isReadOnly()
        assert sequence.text() == "7"
        assert dialog.findChild(QLineEdit, "saved_secret_identifier").text() == "demo/db-AbCdEf"
        dialog.findChild(QPlainTextEdit, "saved_secret_value").setPlainText("edited-local-value")
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons.button(QDialogButtonBox.StandardButton.Save).text() == "저장"
        assert buttons.button(QDialogButtonBox.StandardButton.Cancel).text() == "취소"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    parent = SecretsPage(FakeSecrets(), ImmediateRunner())  # type: ignore[arg-type]
    assert _prompt_saved_secret(parent, saved) == (identifier, "edited-local-value")

    def cancel(dialog: QDialog) -> int:
        assert dialog.windowTitle() == "Secret 등록"
        assert dialog.findChild(QLineEdit, "saved_secret_sequence").isReadOnly()
        assert (
            not dialog.findChild(QDialogButtonBox)
            .button(QDialogButtonBox.StandardButton.Save)
            .isEnabled()
        )
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", cancel)
    assert _prompt_saved_secret(parent, None) is None
