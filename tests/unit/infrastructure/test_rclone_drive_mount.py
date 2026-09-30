import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.s3_drive import DriveState, S3Drive
from aws_connect.infrastructure.rclone_drive_mount import (
    RcloneDriveMount,
    _Mount,
    cache_is_dirty,
    remote_environment,
)


@pytest.fixture
def mounted(tmp_path: Path, monkeypatch):
    adapter = RcloneDriveMount(tmp_path / "rclone.exe", tmp_path / "cache", Mock())
    drive = S3Drive(1, 1, "project", "test-bucket", "assets", "S", "Brand")
    process = Mock()
    process.poll.return_value = None
    mount = _Mount(
        process,
        Mock(),
        io.BytesIO(),
        1,
        "fixture-only",
        "source:test-bucket/assets",
        True,
        DriveState.CONNECTED,
    )
    adapter._mounts[1] = mount
    root = adapter._root(drive)
    root.mkdir(parents=True)
    (root / "pending.json").write_text("{}")
    monkeypatch.setattr(adapter, "available_letters", lambda: set())
    return adapter, drive, mount, root


@pytest.mark.parametrize(
    "metadata,dirty",
    [('{"Dirty": true}', True), ('{"Dirty": false}', False), ("{}", True), ("broken", True)],
)
def test_dirty_cache_is_fail_closed(tmp_path, metadata, dirty):
    meta = tmp_path / "vfsMeta/source/test-bucket"
    meta.mkdir(parents=True)
    (meta / "file.txt").write_text(metadata)
    assert cache_is_dirty(tmp_path) is dirty


def test_remote_configuration_keeps_nested_local_prefix_separate_and_no_inherited_aws(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "unrelated")
    monkeypatch.setenv("RCLONE_CONFIG_SOURCE_ENDPOINT", "https://untrusted.invalid")
    drive = S3Drive(1, 1, "project", "test-bucket", "a path", "S", "Brand/Project")
    credentials = PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value", "fixture-token")
    env, source, remote = remote_environment(drive, credentials, "ap-northeast-2")
    assert source == "source:test-bucket/a path"
    assert remote == "folder1:"
    assert json.loads(env["RCLONE_CONFIG_FOLDER0_UPSTREAMS"]) == "Project=source:test-bucket/a path"
    assert json.loads(env["RCLONE_CONFIG_FOLDER1_UPSTREAMS"]) == "Brand=folder0:"
    assert "AWS_PROFILE" not in env
    assert "RCLONE_CONFIG_SOURCE_ENDPOINT" not in env


def test_actual_drive_absence_and_process_death_are_errors(mounted, monkeypatch):
    adapter, drive, mount, root = mounted
    monkeypatch.setattr(adapter, "available_letters", lambda: {"S"})
    assert adapter.status(drive).message_code == "drive.observe.failed"
    mount.process.poll.return_value = 1
    assert adapter.status(drive).message_code == "drive.process.failed"
    with pytest.raises(ConfigurationError):
        adapter.disconnect(drive)
    assert (root / "pending.json").exists()


def test_cache_full_and_failed_upload_are_visible(mounted, monkeypatch):
    adapter, drive, mount, _ = mounted
    monkeypatch.setattr(adapter, "_call", lambda *_: {"diskCache": {"outOfSpace": True}})
    assert adapter.status(drive).message_code == "drive.cache.full"
    monkeypatch.setattr(adapter, "_call", lambda *_: {"diskCache": {"erroredFiles": 1}})
    assert adapter.status(drive).message_code == "drive.transfer.failed"


def test_unknown_queue_blocks_stop_and_preserves_ownership(mounted, monkeypatch):
    adapter, drive, mount, root = mounted
    monkeypatch.setattr(adapter, "_call", lambda *_: {})
    with pytest.raises(ConfigurationError, match="drive.cache.pending"):
        adapter.disconnect(drive)
    assert adapter._mounts[1] is mount
    assert (root / "pending.json").exists()
    mount.job.close.assert_not_called()


def test_clean_disconnect_requires_unmount_observation_then_releases(mounted, monkeypatch):
    adapter, drive, mount, root = mounted
    calls = []

    def call(_mount, method, payload=None):
        calls.append(method)
        return {"diskCache": {"uploadsQueued": 0, "uploadsInProgress": 0, "erroredFiles": 0}}

    monkeypatch.setattr(adapter, "_call", call)
    monkeypatch.setattr(adapter, "available_letters", lambda: {"S"})
    adapter.disconnect(drive)
    assert calls == ["vfs/stats", "mount/unmount", "core/quit"]
    assert not (root / "pending.json").exists()
    assert not adapter._mounts
    mount.job.close.assert_called_once()


def test_writer_racing_unmount_prevents_cache_removal(mounted, monkeypatch):
    adapter, drive, mount, root = mounted

    def call(_mount, method, payload=None):
        if method == "mount/unmount":
            meta = root / "vfsMeta"
            meta.mkdir()
            (meta / "file").write_text('{"Dirty": true}')
        return {"diskCache": {"uploadsQueued": 0, "uploadsInProgress": 0, "erroredFiles": 0}}

    monkeypatch.setattr(adapter, "_call", call)
    monkeypatch.setattr(adapter, "available_letters", lambda: {"S"})
    with pytest.raises(ConfigurationError):
        adapter.disconnect(drive)
    assert (root / "pending.json").exists()
    mount.job.close.assert_not_called()


def test_disconnect_waits_for_windows_drive_removal(mounted, monkeypatch):
    adapter, drive, mount, root = mounted
    observations = iter([set(), set(), {"S"}])
    monkeypatch.setattr(adapter, "available_letters", lambda: next(observations))
    monkeypatch.setattr("aws_connect.infrastructure.rclone_drive_mount.time.sleep", lambda _: None)
    monkeypatch.setattr(
        adapter,
        "_call",
        lambda *args: {
            "diskCache": {"uploadsQueued": 0, "uploadsInProgress": 0, "erroredFiles": 0}
        },
    )
    adapter.disconnect(drive)
    assert not adapter._mounts
    assert not (root / "pending.json").exists()
