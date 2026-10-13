from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.integration.infrastructure.test_sqlite_profile_store import profile

from aws_connect.application.operations import OperationContext
from aws_connect.application.s3_drive_ports import S3DriveMount
from aws_connect.application.s3_drive_service import S3DriveService
from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.s3_drive import DriveState, DriveStatus, S3Drive
from aws_connect.infrastructure.sqlite_profile_store import SqliteProfileStore


@pytest.fixture
def drives(tmp_path: Path):
    store = SqliteProfileStore(tmp_path / "state.db")
    owner = store.create(profile("owner"))
    other = store.create(profile("other"))
    profiles, sessions, mount = Mock(), Mock(), Mock(spec=S3DriveMount)
    profiles.resolve.side_effect = lambda key: store.get(int(key))
    mount.available_letters.return_value = set("STU")
    mount.status.side_effect = lambda drive: DriveStatus(drive, DriveState.DISCONNECTED)
    service = S3DriveService(profiles, sessions, store, mount)
    saved = service.save(S3Drive(None, owner.require_id(), "project", "test-bucket", "", "S"))
    return service, saved, other, store, mount, sessions


@pytest.mark.parametrize(
    "prefix,local,uri,path",
    [
        ("", "", "s3://test-bucket/", "S:\\"),
        ("brand/project/", "Brand", "s3://test-bucket/brand/project/", "S:\\Brand"),
        ("", "Brand/Assets", "s3://test-bucket/", "S:\\Brand\\Assets"),
    ],
)
def test_mapping_preserves_s3_target(prefix, local, uri, path):
    drive = S3Drive(None, 1, "project", "test-bucket", prefix, "s:", local)
    assert drive.uri == uri
    assert drive.windows_path == path


@pytest.mark.parametrize("path", ["..", "a/../b", "CON", "NUL.txt", "a//b", "a:", "a.", "a=b"])
def test_invalid_windows_prefix(path):
    with pytest.raises(ConfigurationError):
        S3Drive(None, 1, "project", "test-bucket", "", "S", path)


def test_persistence_edit_no_automatic_connection_and_profile_isolation(drives):
    service, saved, other, store, mount, _ = drives
    service.save(replace(saved, prefix="brand", local_prefix="Brand"))
    reopened = SqliteProfileStore(store.path)
    assert reopened.get_drive(saved.id).prefix == "brand"
    assert service.list(other.id) == []
    mount.connect.assert_not_called()
    with pytest.raises(ConfigurationError, match="drive.owner.invalid"):
        service.save(replace(saved, profile_id=other.id))


def test_connect_uses_saved_owner_and_single_operation_context(drives):
    service, saved, _, _, mount, sessions = drives
    context = OperationContext()
    service.connect(saved.id, context)
    sessions.require_credentials.assert_called_once_with(saved.profile_id)
    mount.connect.assert_called_once_with(
        saved, sessions.require_credentials.return_value, "ap-northeast-2", context
    )


def test_busy_letter_save_and_late_connect_collision(drives):
    service, saved, _, _, mount, _ = drives
    mount.available_letters.return_value = set()
    with pytest.raises(ConfigurationError, match="drive.letter.busy"):
        service.save(replace(saved, letter="T"))
    mount.connect.side_effect = ConfigurationError("drive.letter.busy", "late collision")
    with pytest.raises(ConfigurationError, match="drive.letter.busy"):
        service.connect(saved.id, OperationContext())


def test_pending_blocks_delete_edit_profile_and_shutdown(drives):
    service, saved, _, store, mount, _ = drives
    failure = ConfigurationError("drive.cache.pending", "pending")
    mount.disconnect.side_effect = failure
    mount.assert_editable.side_effect = failure
    for operation in (
        lambda: service.delete(saved.id),
        lambda: service.save(replace(saved, letter="T")),
        service.shutdown,
    ):
        with pytest.raises(ConfigurationError, match="drive.cache.pending"):
            operation()
    with pytest.raises(ConfigurationError), service.profile_deletion(saved.profile_id):
        store.delete(saved.profile_id)
    assert store.get_drive(saved.id) == saved
    assert store.get(saved.profile_id) is not None


def test_reset_allows_reconnect_but_pending_cache_still_blocks_destructive_operations(drives):
    service, saved, _, store, mount, _ = drives
    mount.assert_editable.side_effect = ConfigurationError("drive.cache.pending", "Unsent data")
    service.disconnect(saved.id)
    service.connect(saved.id, OperationContext())
    service.shutdown()
    for operation in (
        lambda: service.delete(saved.id),
        lambda: service.save(replace(saved, letter="T")),
    ):
        with pytest.raises(ConfigurationError, match="drive.cache.pending"):
            operation()
    with pytest.raises(ConfigurationError), service.profile_deletion(saved.profile_id):
        store.delete(saved.profile_id)
    assert store.get_drive(saved.id) == saved
    assert store.get(saved.profile_id) is not None


def test_shutdown_rejects_new_connections_after_preserving_recovery_cache(drives):
    service, saved, _, _, mount, _ = drives
    mount.assert_editable.side_effect = ConfigurationError("drive.cache.pending", "Unsent data")
    service.shutdown()
    mount.disconnect.assert_called_once_with(saved)
    mount.assert_editable.assert_not_called()
    with pytest.raises(ConfigurationError, match="drive.closing"):
        service.connect(saved.id, OperationContext())


def test_shutdown_attempts_other_drives_after_failure_and_can_retry(drives):
    service, saved, other, store, mount, _ = drives
    second = service.save(S3Drive(None, other.id, "second", "test-bucket", "", "T"))
    mount.disconnect.side_effect = [ConfigurationError("drive.cache.pending", "pending"), None]
    with pytest.raises(ConfigurationError):
        service.shutdown()
    assert mount.disconnect.call_count == 2
    mount.disconnect.side_effect = None
    service.connect(second.id, OperationContext())
    service.shutdown()
    with pytest.raises(ConfigurationError, match="drive.closing"):
        service.connect(saved.id, OperationContext())


def test_delete_cascade_after_safe_cleanup(drives):
    service, saved, _, store, mount, _ = drives
    with service.profile_deletion(saved.profile_id):
        store.delete(saved.profile_id)
    assert store.get_drive(saved.id) is None
    mount.disconnect.assert_called_once_with(saved)


def test_clone_copies_mapping_with_unused_letter_and_never_connects(drives):
    service, saved, _, _, mount, _ = drives
    source = service.save(replace(saved, prefix="assets", local_prefix="Files"))
    clone = service.clone(source.id, "copy")
    assert clone == replace(source, id=clone.id, name="copy", letter="T")
    assert clone.id != source.id
    assert service.list(source.profile_id)[-1].state is DriveState.DISCONNECTED
    mount.connect.assert_not_called()
    mount.available_letters.return_value = {"S", "T"}
    with pytest.raises(ConfigurationError, match="drive.letter.busy"):
        service.clone(source.id, "no space")


def test_rename_uses_edit_guard_and_explorer_requires_connected_path(drives):
    service, saved, _, _, mount, _ = drives
    source = service.save(replace(saved, local_prefix="Files/Brand"))
    renamed = service.rename(source.id, "renamed")
    assert renamed == replace(source, name="renamed")
    with pytest.raises(ConfigurationError, match="drive.not_connected"):
        service.connected_path(source.id)
    mount.status.side_effect = lambda drive: DriveStatus(drive, DriveState.CONNECTED)
    assert service.connected_path(source.id) == "S:\\Files\\Brand"
    mount.assert_editable.side_effect = ConfigurationError("drive.active", "Disconnect first")
    with pytest.raises(ConfigurationError, match="drive.active"):
        service.rename(source.id, "blocked")
