import hashlib
import json
from unittest.mock import Mock

import pytest

from aws_connect.application.winfsp_runtime import RuntimeState, WinFspStatus
from aws_connect.domain.errors import ConfigurationError
from aws_connect.infrastructure import winfsp_installer as module


def test_bad_payload_never_reaches_elevation(tmp_path, monkeypatch):
    payload = tmp_path / "bad.msi"
    payload.write_bytes(b"untrusted")
    adapter = module.WindowsWinFspInstaller(payload, tmp_path / "state")
    launch = Mock()
    monkeypatch.setattr(adapter, "_execute", launch)
    monkeypatch.setattr(module, "powershell", lambda *args, **kwargs: "")
    with pytest.raises(ConfigurationError, match="verification_failed"):
        adapter.install(repair=False)
    launch.assert_not_called()


def test_verified_payload_is_locked_until_installer_returns(tmp_path, monkeypatch):
    payload = tmp_path / "한글 path.msi"
    payload.write_bytes(b"fixture")
    monkeypatch.setattr(module, "WINFSP_SHA256", hashlib.sha256(b"fixture").hexdigest())
    monkeypatch.setattr(
        module,
        "powershell",
        lambda script, **kwargs: (
            json.dumps({"status": "Valid", "subject": module.WINFSP_SIGNER})
            if "Get-AuthenticodeSignature" in script
            else ""
        ),
    )
    adapter = module.WindowsWinFspInstaller(payload, tmp_path / "state")

    def execute(args):
        assert "/norestart" in args
        assert str(payload.resolve()) in args
        with pytest.raises(OSError):
            payload.write_bytes(b"changed")
        return 1602

    monkeypatch.setattr(adapter, "_execute", execute)
    assert adapter.install(repair=False) == 1602
    payload.write_bytes(b"now unlocked")


def test_bad_signature_never_launches_installer(tmp_path, monkeypatch):
    payload = tmp_path / "fixture.msi"
    payload.write_bytes(b"fixture")
    monkeypatch.setattr(module, "WINFSP_SHA256", hashlib.sha256(b"fixture").hexdigest())
    monkeypatch.setattr(
        module,
        "powershell",
        lambda script, **kwargs: (
            json.dumps({"status": "NotSigned", "subject": ""})
            if "Get-AuthenticodeSignature" in script
            else ""
        ),
    )
    adapter = module.WindowsWinFspInstaller(payload, tmp_path)
    launch = Mock()
    monkeypatch.setattr(adapter, "_execute", launch)
    with pytest.raises(ConfigurationError, match="verification_failed"):
        adapter.install(repair=False)
    launch.assert_not_called()


def test_reboot_marker_survives_app_restart_until_boot_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(module.ctypes.windll.shell32, "IsUserAnAdmin", lambda: False)
    monkeypatch.setattr(module, "boot_identity", lambda: "boot1")
    monkeypatch.setattr(module, "inspect_winfsp", lambda: WinFspStatus(RuntimeState.READY))
    adapter = module.WindowsWinFspInstaller(tmp_path / "payload", tmp_path / "state")
    adapter.remember_reboot()
    assert adapter.inspect().state is RuntimeState.REBOOT_REQUIRED
    monkeypatch.setattr(module, "boot_identity", lambda: "boot2")
    assert adapter.inspect().ready
    assert not (tmp_path / "state/winfsp-reboot.json").exists()


def test_driver_registration_requires_filesystem_driver_file(tmp_path, monkeypatch):
    import winreg
    from contextlib import nullcontext

    driver = tmp_path / "driver.sys"
    values = {"Type": 2, "ImagePath": "\\??\\" + str(driver)}
    monkeypatch.setattr(winreg, "OpenKey", lambda *args: nullcontext("fixture"))
    monkeypatch.setattr(winreg, "QueryInfoKey", lambda *args: (1, 0, 0))
    monkeypatch.setattr(winreg, "EnumKey", lambda *args: "WinFsp+fixture")
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (values[name], 0))
    assert not module._driver_present()
    driver.write_bytes(b"fixture")
    assert module._driver_present()
    values["Type"] = 16  # Launcher alone does not prove the kernel driver is installed.
    assert not module._driver_present()
