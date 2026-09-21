from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_connect.application.operations import (
    CancellationToken,
    OperationCancelled,
    OperationContext,
)
from aws_connect.application.ports import S3Object
from aws_connect.application.s3_service import (
    MULTIPART_THRESHOLD,
    S3LocationService,
    S3Service,
    SaveS3LocationRequest,
    UploadConflictPolicy,
)
from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials
from aws_connect.domain.errors import ConfigurationError, CredentialValidationError, S3TransferError
from aws_connect.domain.s3_location import S3Location


def _services() -> tuple[S3LocationService, S3Service, Mock, Mock]:
    profile = AwsProfile(
        7,
        "dev",
        "ap-northeast-2",
        "123456789012",
        "developer",
        "arn:aws:iam::123456789012:mfa/developer",
        b"a",
        b"b",
    )
    profiles = Mock()
    profiles.resolve.return_value = profile
    sessions = Mock()
    sessions.require_credentials.return_value = PlainCredentials(
        "ACCESSKEYTEST0001", "not-sensitive-test-value", "fixture-token"
    )
    store = Mock()
    gateway = Mock()
    gateway.list_buckets.return_value = ["alpha-bucket", "test-upload-bucket"]
    gateway.list_objects.return_value = [S3Object("reports/", 0, None, True)]
    gateway.object_exists.return_value = False
    return (
        S3LocationService(profiles, store),
        S3Service(profiles, sessions, gateway),
        store,
        gateway,
    )


def test_saved_location_crud_delegates_to_profile_scoped_store() -> None:
    locations, _s3, store, _gateway = _services()
    created = S3Location(3, 7, "reports", "test-upload-bucket", "reports/")
    store.create_s3_location.return_value = created
    store.get_s3_location_by_name.return_value = created

    assert (
        locations.create(SaveS3LocationRequest("dev", "reports", "test-upload-bucket", "reports/"))
        == created
    )
    assert locations.show("reports", "dev") == created
    locations.delete("reports", "dev")
    store.delete_s3_location.assert_called_once_with(3)


def test_saved_location_list_update_and_missing_paths_are_typed() -> None:
    locations, _s3, store, _gateway = _services()
    existing = S3Location(3, 7, "reports", "test-upload-bucket", "reports/")
    store.list_s3_locations.return_value = [existing]
    store.get_s3_location.return_value = existing
    store.update_s3_location.return_value = S3Location(
        3, 7, "uploads", "test-upload-bucket", "incoming/"
    )
    assert locations.list("dev") == [existing]
    assert (
        locations.update(
            SaveS3LocationRequest("dev", "uploads", "test-upload-bucket", "incoming/", 3)
        ).name
        == "uploads"
    )
    with pytest.raises(ConfigurationError, match="s3.location.id.required"):
        locations.update(SaveS3LocationRequest("dev", "uploads", "test-upload-bucket"))
    store.get_s3_location.return_value = S3Location(3, 8, "other", "test-upload-bucket")
    with pytest.raises(ConfigurationError, match="s3.location.not_found"):
        locations.show(3, "dev")


def test_saved_location_update_cannot_transfer_ownership_between_profiles() -> None:
    locations, _s3, store, _gateway = _services()
    store.get_s3_location.return_value = S3Location(3, 8, "other", "test-upload-bucket", "private/")

    with pytest.raises(ConfigurationError, match="s3.location.not_found"):
        locations.update(
            SaveS3LocationRequest("dev", "stolen", "test-upload-bucket", "incoming/", 3)
        )

    store.update_s3_location.assert_not_called()


def test_direct_list_never_uses_bucket_catalog() -> None:
    _locations, service, _store, gateway = _services()

    result = service.list_objects("test-upload-bucket", "reports/", "dev")

    assert result[0].is_prefix
    gateway.list_objects.assert_called_once()
    assert not hasattr(gateway, "list_buckets") or not gateway.list_buckets.called


def test_optional_bucket_catalog_is_independent_from_object_browsing() -> None:
    _locations, service, _store, gateway = _services()

    assert service.list_buckets("dev") == ["alpha-bucket", "test-upload-bucket"]
    gateway.list_buckets.assert_called_once()
    gateway.list_objects.assert_not_called()


def test_delete_object_rejects_prefix_and_delegates_one_exact_key() -> None:
    _locations, service, _store, gateway = _services()

    service.delete_object("test-upload-bucket", "reports/file.txt", "dev")

    gateway.delete_object.assert_called_once()
    assert gateway.delete_object.call_args.args[2:] == (
        "test-upload-bucket",
        "reports/file.txt",
    )
    with pytest.raises(ConfigurationError, match="s3.delete.object.required"):
        service.delete_object("test-upload-bucket", "reports/", "dev")


def test_download_object_validates_target_and_delegates_exact_key(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    destination = tmp_path / "report.txt"

    assert (
        service.download_object("test-upload-bucket", "reports/report.txt", destination, "dev")
        == destination
    )
    assert gateway.download_file.call_args.args[2:] == (
        "test-upload-bucket",
        "reports/report.txt",
        destination,
    )
    with pytest.raises(ConfigurationError, match="s3.download.object.required"):
        service.download_object("test-upload-bucket", "reports/", destination, "dev")


def test_prepare_download_expands_mixed_files_and_prefixes_and_deduplicates_keys(
    tmp_path: Path,
) -> None:
    _locations, service, _store, gateway = _services()
    direct = S3Object("reports/a.txt", 3, None)
    prefix = S3Object("reports/", 0, None, True)
    gateway.list_objects_recursive.return_value = [
        S3Object("reports/", 0, None, True),
        direct,
        S3Object("reports/nested/b.txt", 4, None),
    ]

    plan = service.prepare_download([direct, prefix], tmp_path, "test-upload-bucket", "dev")

    assert [(item.key, item.destination) for item in plan.items] == [
        ("reports/a.txt", tmp_path / "a.txt"),
        ("reports/nested/b.txt", tmp_path / "reports" / "nested" / "b.txt"),
    ]
    gateway.list_objects_recursive.assert_called_once()


@pytest.mark.parametrize(
    ("selected", "code"),
    [
        ([], "s3.download.selection.required"),
        ([S3Object("../escape.txt", 1, None)], "s3.download.key.invalid"),
        (
            [S3Object("reports/a.txt", 1, None), S3Object("reports//a.txt", 1, None)],
            "s3.download.destination.duplicate",
        ),
    ],
)
def test_prepare_download_rejects_empty_traversal_and_local_destination_duplicates(
    tmp_path: Path, selected: list[S3Object], code: str
) -> None:
    _locations, service, _store, _gateway = _services()

    with pytest.raises(ConfigurationError, match=code):
        service.prepare_download(selected, tmp_path, "test-upload-bucket", "dev")


def test_download_reports_serial_progress_and_preserves_partial_failure(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    first = S3Object("reports/a.txt", 3, None)
    second = S3Object("reports/b.txt", 4, None)
    plan = service.prepare_download([first, second], tmp_path, "test-upload-bucket", "dev")
    calls = 0

    def download(*args, **_kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise S3TransferError("s3.transfer.failed", "network")
        args[-2](3)

    gateway.download_file.side_effect = download
    events = []

    with pytest.raises(S3TransferError, match="s3.transfer.failed"):
        service.download(plan, OperationContext(progress=events.append))

    assert gateway.download_file.call_count == 2
    assert events[-1].phase == "failed"
    assert events[-1].completed == 3
    assert events[-1].target == "s3://test-upload-bucket/reports/b.txt"


def test_download_maps_gateway_cancellation_to_operation_cancelled(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    plan = service.prepare_download(
        [S3Object("reports/a.txt", 3, None)], tmp_path, "test-upload-bucket", "dev"
    )
    gateway.download_file.side_effect = S3TransferError("s3.download.cancelled", "cancelled")

    with pytest.raises(OperationCancelled):
        service.download(plan, OperationContext())


def test_prepare_upload_reports_uri_and_skip_policy_omits_existing(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    gateway.object_exists.return_value = True

    plan = service.prepare_upload([source], "test-upload-bucket", "incoming", "dev")

    assert plan.items[0].uri == "s3://test-upload-bucket/incoming/report.txt"
    assert plan.items[0].exists
    result = service.upload(
        plan, policy=UploadConflictPolicy.SKIP_EXISTING, context=OperationContext()
    )
    assert result.skipped == (plan.items[0].uri,)
    gateway.put_file.assert_not_called()


def test_prepare_upload_rejects_duplicate_target_keys(tmp_path: Path) -> None:
    _locations, service, _store, _gateway = _services()
    first = tmp_path / "first" / "report.txt"
    second = tmp_path / "second" / "report.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="s3.upload.target.duplicate"):
        service.prepare_upload([first, second], "test-upload-bucket")


def test_prepare_upload_recurses_folders_preserves_relative_keys_and_deduplicates_sources(
    tmp_path: Path,
) -> None:
    _locations, service, _store, gateway = _services()
    folder = tmp_path / "folder"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    first = folder / "first.txt"
    second = nested / "second.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    plan = service.prepare_upload([folder, first], "test-upload-bucket", "incoming", "dev")

    assert [(item.source, item.key) for item in plan.items] == [
        (first.resolve(), "incoming/first.txt"),
        (second.resolve(), "incoming/nested/second.txt"),
    ]
    assert gateway.object_exists.call_count == 2


def test_prepare_upload_rejects_symbolic_links(tmp_path: Path) -> None:
    _locations, service, _store, _gateway = _services()
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("Symbolic links are unavailable on this Windows host")

    with pytest.raises(ConfigurationError, match="s3.upload.source.symlink"):
        service.prepare_upload([link], "test-upload-bucket")


def test_prepare_upload_rejects_empty_missing_and_unauthenticated_sources(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    with pytest.raises(ConfigurationError, match="s3.upload.source.required"):
        service.prepare_upload([], "test-upload-bucket")
    with pytest.raises(ConfigurationError, match="s3.upload.source.invalid"):
        service.prepare_upload([tmp_path / "missing.txt"], "test-upload-bucket")
    service._sessions.require_credentials.side_effect = CredentialValidationError(
        "auth.mfa_required", "test"
    )
    with pytest.raises(CredentialValidationError, match="auth.mfa_required"):
        service.list_objects("test-upload-bucket")
    gateway.object_exists.assert_not_called()


def test_upload_uses_progress_and_cancellation_contract(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    small = tmp_path / "small.txt"
    small.write_bytes(b"1234")
    plan = service.prepare_upload([small], "test-upload-bucket", profile="dev")
    events = []
    gateway.put_file.side_effect = lambda *_args: _args[-2](4)

    context = OperationContext(operation_id="upload-one", progress=events.append)
    result = service.upload(plan, overwrite=False, context=context)

    assert result.bytes_transferred == 4
    assert events[0].operation_id == context.operation_id == events[-1].operation_id
    assert events[1].target == "s3://test-upload-bucket/small.txt"
    assert any(event.message_code == "s3.upload.progress" for event in events)

    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        service.upload(plan, overwrite=False, context=OperationContext(cancellation=token))


def test_multiple_files_upload_in_request_order(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    plan = service.prepare_upload([first, second], "test-upload-bucket", "incoming", "dev")
    gateway.put_file.side_effect = lambda *_args: _args[-2](3)

    result = service.upload(plan, overwrite=False, context=OperationContext())

    assert result.uploaded == (
        "s3://test-upload-bucket/incoming/first.txt",
        "s3://test-upload-bucket/incoming/second.txt",
    )
    assert gateway.put_file.call_count == 2


def test_skip_existing_rechecks_each_transfer_and_reports_late_collision(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    gateway.object_exists.side_effect = (False, False, True, False)
    plan = service.prepare_upload([first, second], "test-upload-bucket", "incoming", "dev")
    gateway.put_file.side_effect = lambda *_args: _args[-2](3)

    result = service.upload(
        plan, policy=UploadConflictPolicy.SKIP_EXISTING, context=OperationContext()
    )

    assert result.uploaded == ("s3://test-upload-bucket/incoming/second.txt",)
    assert result.skipped == ("s3://test-upload-bucket/incoming/first.txt",)
    assert result.bytes_transferred == 3
    assert gateway.put_file.call_count == 1


def test_multi_file_failure_preserves_completed_file_summary(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    plan = service.prepare_upload([first, second], "test-upload-bucket", "incoming", "dev")

    def transfer(*args) -> None:
        if args[3].endswith("second.txt"):
            raise S3TransferError("s3.transfer.failed", "network")
        args[-2](3)

    gateway.put_file.side_effect = transfer

    events = []
    with pytest.raises(S3TransferError, match="s3.transfer.failed"):
        service.upload(
            plan,
            overwrite=False,
            context=OperationContext(progress=events.append),
        )

    assert [event for event in events if event.message_code == "s3.upload.progress"][
        -1
    ].completed == 3
    assert gateway.put_file.call_count == 2


def test_cancellation_requested_during_transfer_maps_to_cancelled(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    source = tmp_path / "source.txt"
    source.write_bytes(b"data")
    plan = service.prepare_upload([source], "test-upload-bucket")
    token = CancellationToken()

    def complete_then_cancel(*_args) -> None:
        token.cancel()

    gateway.put_file.side_effect = complete_then_cancel
    with pytest.raises(OperationCancelled):
        service.upload(plan, overwrite=False, context=OperationContext(cancellation=token))

    token = CancellationToken()
    gateway.put_file.side_effect = S3TransferError("s3.upload.cancelled", "cancelled")
    with pytest.raises(OperationCancelled):
        service.upload(plan, overwrite=False, context=OperationContext(cancellation=token))


def test_large_upload_uses_bounded_multipart_and_preserves_typed_failure(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    large = tmp_path / "large.bin"
    with large.open("wb") as stream:
        stream.truncate(MULTIPART_THRESHOLD)
    plan = service.prepare_upload([large], "test-upload-bucket", profile="dev")
    gateway.multipart_file.side_effect = S3TransferError("s3.transfer.failed", "network")

    with pytest.raises(S3TransferError, match="s3.transfer.failed"):
        service.upload(plan, overwrite=False, context=OperationContext())
    args = gateway.multipart_file.call_args.args
    assert args[6] == 1


def test_explicit_retry_of_failed_multipart_starts_fresh_operation_and_progress(
    tmp_path: Path,
) -> None:
    _locations, service, _store, gateway = _services()
    large = tmp_path / "large.bin"
    with large.open("wb") as stream:
        stream.truncate(MULTIPART_THRESHOLD)
    plan = service.prepare_upload([large], "test-upload-bucket", profile="dev")
    attempts = 0

    def transfer(*args) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise S3TransferError("s3.transfer.failed", "network", retryable=True)
        args[-2](MULTIPART_THRESHOLD)

    gateway.multipart_file.side_effect = transfer
    first_events = []
    second_events = []

    first_context = OperationContext(operation_id="upload-first", progress=first_events.append)
    second_context = OperationContext(operation_id="upload-second", progress=second_events.append)
    with pytest.raises(S3TransferError, match="s3.transfer.failed"):
        service.upload(plan, overwrite=False, context=first_context)
    retried = service.upload(plan, overwrite=False, context=second_context)

    assert retried.bytes_transferred == MULTIPART_THRESHOLD
    assert first_context.operation_id != second_context.operation_id
    assert first_events[0].completed == 0
    assert second_events[0].completed == 0
    assert {event.operation_id for event in first_events} == {first_context.operation_id}
    assert {event.operation_id for event in second_events} == {second_context.operation_id}
    assert gateway.multipart_file.call_count == 2


def test_legacy_overwrite_false_maps_to_skip_existing_for_stale_plan(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    gateway.object_exists.side_effect = (False, True)
    plan = service.prepare_upload([source], "test-upload-bucket")

    result = service.upload(plan, overwrite=False, context=OperationContext())

    assert result.skipped == (plan.items[0].uri,)
    gateway.put_file.assert_not_called()


@pytest.mark.parametrize("policy", list(UploadConflictPolicy))
def test_download_existing_file_requires_policy_and_reports_skips(tmp_path: Path, policy) -> None:
    _locations, service, _store, gateway = _services()
    target = tmp_path / "existing.txt"
    target.write_text("original", encoding="utf-8")
    plan = service.prepare_download(
        [S3Object("existing.txt", 3, None)], tmp_path, "test-upload-bucket", "dev"
    )
    assert plan.items[0].exists
    result = service.download(plan, OperationContext(), policy=policy)
    if policy is UploadConflictPolicy.SKIP_EXISTING:
        gateway.download_file.assert_not_called()
        assert result.skipped == (target,)
        assert result.downloaded == ()
    else:
        assert gateway.download_file.call_args.kwargs["overwrite"] is True
        assert result.downloaded == (target,)


def test_download_new_conflict_after_preflight_cannot_be_overwritten(tmp_path: Path) -> None:
    _locations, service, _store, gateway = _services()
    plan = service.prepare_download(
        [S3Object("new.txt", 3, None)], tmp_path, "test-upload-bucket", "dev"
    )
    plan.items[0].destination.write_text("newly-created", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="s3.download.destination.exists"):
        service.download(plan, OperationContext(), policy=UploadConflictPolicy.OVERWRITE)
    gateway.download_file.assert_not_called()


def test_selected_nested_files_download_directly_and_reject_basename_collision(tmp_path):
    _, service, _, gateway = _services()
    plan = service.prepare_download(
        [S3Object("images/banners/logo.png", 3, None)], tmp_path, "test-upload-bucket"
    )
    assert plan.items[0].destination == tmp_path / "logo.png"
    service.download(plan, OperationContext())
    assert gateway.download_file.call_args.args[4] == tmp_path / "logo.png"
    with pytest.raises(ConfigurationError, match="s3.download.destination.duplicate"):
        service.prepare_download(
            [S3Object("first/logo.png", 3, None), S3Object("second/logo.png", 3, None)],
            tmp_path,
            "test-upload-bucket",
        )
    gateway.download_file.assert_called_once()
