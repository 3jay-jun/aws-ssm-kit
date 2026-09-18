from pathlib import Path

import pytest
from tests.unit.application.test_s3_service import _services

from aws_connect.application.operations import OperationContext
from aws_connect.application.ports import S3Object
from aws_connect.domain.errors import ConfigurationError, S3TransferError


def test_search_uses_recursive_listing_and_only_matches_file_names() -> None:
    _, service, _, gateway = _services()
    gateway.list_objects_recursive.return_value = [
        S3Object("images/sub/LOGO.png", 1, None),
        S3Object("images/logo/a.txt", 2, None),
        S3Object("images/logo/", 0, None, True),
    ]
    result = service.list_objects("test-upload-bucket", "images/", "dev", query="logo")
    assert [item.key for item in result] == ["images/sub/LOGO.png"]
    gateway.list_objects.assert_not_called()


def test_rename_preserves_parent_and_rejects_collision_and_folder() -> None:
    _, service, _, gateway = _services()
    assert service.rename_object("test-upload-bucket", "a/old.txt", "new.txt", "dev") == "a/new.txt"
    assert gateway.rename_object.call_args.args[-2:] == ("a/old.txt", "a/new.txt")
    gateway.object_exists.return_value = True
    with pytest.raises(ConfigurationError, match="s3.rename.exists"):
        service.rename_object("test-upload-bucket", "a/old.txt", "taken.txt", "dev")
    for key, name in (("folder/", "new"), ("old", "../new"), ("old", "")):
        with pytest.raises(ConfigurationError, match="s3.rename.invalid"):
            service.rename_object("test-upload-bucket", key, name, "dev")
    assert gateway.rename_object.call_count == 1


def test_item_events_preserve_success_before_later_failure(tmp_path: Path) -> None:
    _, service, _, gateway = _services()
    files = [tmp_path / "a.txt", tmp_path / "b.txt"]
    for source in files:
        source.write_text("one")
    plan = service.prepare_upload(files, "test-upload-bucket", profile="dev")

    def transfer(*args) -> None:
        if args[3] == "b.txt":
            raise S3TransferError("s3.transfer.failed", "failed")
        args[-2](3)

    gateway.put_file.side_effect = transfer
    events = []
    with pytest.raises(S3TransferError):
        service.upload(plan, overwrite=False, context=OperationContext(progress=events.append))
    done = [e.target for e in events if e.message_code == "s3.upload.item.completed"]
    assert done == ["s3://test-upload-bucket/a.txt"]
