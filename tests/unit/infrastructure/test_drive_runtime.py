import winreg
from unittest.mock import Mock

import pytest

from aws_connect.application.operations import OperationContext
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.s3_drive import S3Drive
from aws_connect.infrastructure.rclone_drive_mount import RcloneDriveMount
from aws_connect.infrastructure.windows_drive_runtime import winfsp_available


def test_winfsp_probes_both_registry_views(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/winfsp-x64.dll").touch()
    calls = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def open_key(root, path, reserved, access):
        calls.append(access)
        if access & winreg.KEY_WOW64_32KEY:
            raise FileNotFoundError
        return Key()

    monkeypatch.setattr(winreg, "OpenKey", open_key)
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *_: (str(tmp_path), winreg.REG_SZ))
    assert winfsp_available()
    assert len(calls) == 2


def test_missing_components_have_distinct_errors_before_launch(tmp_path, monkeypatch):
    executable = tmp_path / "rclone.exe"
    adapter = RcloneDriveMount(executable, tmp_path / "cache", Mock())
    drive = S3Drive(1, 1, "test", "test-bucket", "", "S")
    credentials = PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value")
    with pytest.raises(ConfigurationError, match="drive.runtime.rclone_missing"):
        adapter.connect(drive, credentials, "ap-northeast-2", OperationContext())
    executable.touch()
    monkeypatch.setattr(
        "aws_connect.infrastructure.rclone_drive_mount.winfsp_available", lambda: False
    )
    with pytest.raises(ConfigurationError, match="drive.runtime.winfsp_missing"):
        adapter.connect(drive, credentials, "ap-northeast-2", OperationContext())
    assert not (tmp_path / "cache").exists()
