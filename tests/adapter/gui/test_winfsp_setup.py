from unittest.mock import Mock

from PySide6.QtWidgets import QMessageBox
from tests.adapter.gui.test_shell import ImmediateTaskRunner

from aws_connect.application.winfsp_runtime import InstallResult, RuntimeState, WinFspStatus
from aws_connect.presentation.gui.winfsp_setup import WinFspSetupWidget


def test_cancel_keeps_retry_available_without_claiming_ready(qt_application, monkeypatch):
    service = Mock()
    missing = WinFspStatus(RuntimeState.MISSING)
    service.inspect.return_value = missing
    service.install.return_value = InstallResult("cancelled", missing, 1223)
    widget = WinFspSetupWidget(service, ImmediateTaskRunner())
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    widget.refresh()
    assert widget.install_button.isEnabled()
    widget.install_button.click()
    assert "취소" in widget.label.text()
    assert widget.install_button.isEnabled()
    assert not widget._busy


def test_repair_requires_confirmation_and_reboot_disables_install(qt_application, monkeypatch):
    service = Mock()
    service.inspect.return_value = WinFspStatus(RuntimeState.REPAIR_REQUIRED, "2.0")
    service.install.return_value = InstallResult(
        "reboot_required", WinFspStatus(RuntimeState.REBOOT_REQUIRED), 3010
    )
    widget = WinFspSetupWidget(service, ImmediateTaskRunner())
    widget.refresh()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    widget.install_button.click()
    service.install.assert_not_called()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    widget.install_button.click()
    service.install.assert_called_once_with(confirm_replace=True)
    assert "재부팅" in widget.label.text()
    assert not widget.install_button.isEnabled()
