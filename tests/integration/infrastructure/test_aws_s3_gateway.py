from pathlib import Path
from unittest.mock import Mock

import boto3
import pytest
from botocore.stub import Stubber

from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import AwsNetworkError, AwsPermissionError, S3TransferError
from aws_connect.infrastructure.aws_s3_gateway import Boto3S3Gateway


def _credentials() -> PlainCredentials:
    return PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value", "fixture-token")


def _client():
    return boto3.client(
        "s3",
        region_name="ap-northeast-2",
        aws_access_key_id="ACCESSKEYTEST0001",
        aws_secret_access_key="not-sensitive-test-value",  # pragma: allowlist secret
        aws_session_token="fixture-token",
    )


def test_list_objects_uses_delimiter_empty_prefix_and_paginates() -> None:
    client = _client()
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)
    stubber = Stubber(client)
    stubber.add_response(
        "list_objects_v2",
        {
            "IsTruncated": True,
            "Name": "test-upload-bucket",
            "Prefix": "",
            "MaxKeys": 1000,
            "KeyCount": 1,
            "NextContinuationToken": "next",
            "CommonPrefixes": [{"Prefix": "reports/"}],
        },
        {"Bucket": "test-upload-bucket", "Prefix": "", "Delimiter": "/"},
    )
    stubber.add_response(
        "list_objects_v2",
        {
            "IsTruncated": False,
            "Name": "test-upload-bucket",
            "Prefix": "",
            "MaxKeys": 1000,
            "KeyCount": 1,
            "Contents": [
                {"Key": "readme.txt", "Size": 3, "ETag": '"etag"', "StorageClass": "STANDARD"}
            ],
        },
        {
            "Bucket": "test-upload-bucket",
            "Prefix": "",
            "Delimiter": "/",
            "ContinuationToken": "next",
        },
    )
    with stubber:
        values = gateway.list_objects(_credentials(), "ap-northeast-2", "test-upload-bucket", "")
    assert [(value.key, value.is_prefix) for value in values] == [
        ("reports/", True),
        ("readme.txt", False),
    ]


def test_optional_list_buckets_returns_sorted_names_and_maps_permission() -> None:
    client = _client()
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)
    available = Stubber(client)
    available.add_response(
        "list_buckets",
        {"Buckets": [{"Name": "zeta"}, {"Name": "alpha"}]},
        {},
    )
    with available:
        assert gateway.list_buckets(_credentials(), "ap-northeast-2") == ["alpha", "zeta"]

    denied = Stubber(client)
    denied.add_client_error("list_buckets", service_error_code="AccessDenied")
    with denied, pytest.raises(AwsPermissionError) as caught:
        gateway.list_buckets(_credentials(), "ap-northeast-2")
    assert caught.value.aws_action == "ListBuckets"


def test_existence_check_uses_list_permission_and_maps_permission() -> None:
    client = _client()
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)
    missing = Stubber(client)
    missing.add_response(
        "list_objects_v2",
        {
            "IsTruncated": False,
            "Name": "test-upload-bucket",
            "Prefix": "new.txt",
            "MaxKeys": 1,
            "KeyCount": 0,
        },
        {"Bucket": "test-upload-bucket", "Prefix": "new.txt", "MaxKeys": 1},
    )
    with missing:
        assert not gateway.object_exists(
            _credentials(), "ap-northeast-2", "test-upload-bucket", "new.txt"
        )
    denied = Stubber(client)
    denied.add_client_error(
        "list_objects_v2",
        service_error_code="AccessDenied",
        expected_params={
            "Bucket": "test-upload-bucket",
            "Prefix": "private.txt",
            "MaxKeys": 1,
        },
    )
    with denied, pytest.raises(AwsPermissionError):
        gateway.object_exists(_credentials(), "ap-northeast-2", "test-upload-bucket", "private.txt")


def test_delete_object_uses_exact_bucket_and_key() -> None:
    client = _client()
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)
    stubber = Stubber(client)
    stubber.add_response(
        "delete_object",
        {},
        {"Bucket": "test-upload-bucket", "Key": "reports/file.txt"},
    )

    with stubber:
        gateway.delete_object(
            _credentials(), "ap-northeast-2", "test-upload-bucket", "reports/file.txt"
        )


def test_download_file_replaces_destination_and_cleans_temporary_file(tmp_path: Path) -> None:
    client = Mock()

    def download(_bucket: str, _key: str, filename: str) -> None:
        Path(filename).write_bytes(b"downloaded")

    client.download_file.side_effect = download
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)
    destination = tmp_path / "report.txt"

    gateway.download_file(
        _credentials(),
        "ap-northeast-2",
        "test-upload-bucket",
        "reports/report.txt",
        destination,
    )

    assert destination.read_bytes() == b"downloaded"
    assert list(tmp_path.glob(".aws-connect-*.download")) == []


def test_multipart_failure_and_cancel_always_attempt_abort_without_hiding_original(
    tmp_path: Path,
) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"123456")
    client = Mock()
    client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
    client.upload_part.side_effect = RuntimeError("wire failed")
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)

    with pytest.raises(S3TransferError, match="s3.transfer.failed") as caught:
        gateway.multipart_file(
            _credentials(),
            "ap-northeast-2",
            "test-upload-bucket",
            "large.bin",
            source,
            5 * 1024 * 1024,
            2,
            lambda _size: None,
            lambda: False,
        )
    client.abort_multipart_upload.assert_called_once()
    assert "RuntimeError" in caught.value.technical_cause

    client.reset_mock()
    client.create_multipart_upload.return_value = {"UploadId": "upload-2"}
    client.abort_multipart_upload.side_effect = RuntimeError("cleanup failed")
    with pytest.raises(S3TransferError, match="s3.upload.cancelled") as cancelled:
        gateway.multipart_file(
            _credentials(),
            "ap-northeast-2",
            "test-upload-bucket",
            "large.bin",
            source,
            5 * 1024 * 1024,
            2,
            lambda _size: None,
            lambda: True,
        )
    assert "AbortMultipartUpload failed" in cancelled.value.technical_cause


def test_multipart_aws_network_failure_is_typed_and_aborted(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"123456")
    client = _client()
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)
    stubber = Stubber(client)
    stubber.add_response(
        "create_multipart_upload",
        {"UploadId": "upload-1"},
        {"Bucket": "test-upload-bucket", "Key": "large.bin"},
    )
    stubber.add_client_error("upload_part", service_error_code="InternalError")
    stubber.add_response(
        "abort_multipart_upload",
        {},
        {"Bucket": "test-upload-bucket", "Key": "large.bin", "UploadId": "upload-1"},
    )
    with stubber, pytest.raises(AwsNetworkError):
        gateway.multipart_file(
            _credentials(),
            "ap-northeast-2",
            "test-upload-bucket",
            "large.bin",
            source,
            5 * 1024 * 1024,
            1,
            lambda _size: None,
            lambda: False,
        )


@pytest.mark.parametrize(
    ("failed_method", "expected_action", "expects_abort"),
    (
        ("create_multipart_upload", "CreateMultipartUpload", False),
        ("complete_multipart_upload", "CompleteMultipartUpload", True),
    ),
)
def test_multipart_failure_reports_exact_primary_action(
    tmp_path: Path, failed_method: str, expected_action: str, expects_abort: bool
) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"123456")
    client = Mock()
    client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
    client.upload_part.return_value = {"ETag": '"one"'}
    getattr(client, failed_method).side_effect = RuntimeError("wire failed")
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)

    with pytest.raises(S3TransferError) as caught:
        gateway.multipart_file(
            _credentials(),
            "ap-northeast-2",
            "test-upload-bucket",
            "large.bin",
            source,
            5 * 1024 * 1024,
            1,
            lambda _size: None,
            lambda: False,
        )

    assert caught.value.aws_action == expected_action
    assert client.abort_multipart_upload.called is expects_abort


def test_put_and_multipart_success_report_bytes_with_bounded_parts(tmp_path: Path) -> None:
    small = tmp_path / "small.bin"
    small.write_bytes(b"1234")
    client = Mock()

    def consume(**kwargs):
        assert kwargs["Body"].read() == b"1234"
        return {}

    client.put_object.side_effect = consume
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)
    progress: list[int] = []
    gateway.put_file(
        _credentials(),
        "ap-northeast-2",
        "test-upload-bucket",
        "small.bin",
        small,
        progress.append,
        lambda: False,
    )
    assert progress == [4]

    large = tmp_path / "large.bin"
    part_size = 5 * 1024 * 1024
    large.write_bytes(b"a" * part_size + b"end")
    client.reset_mock()
    client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
    client.upload_part.side_effect = [{"ETag": '"one"'}, {"ETag": '"two"'}]
    progress.clear()
    gateway.multipart_file(
        _credentials(),
        "ap-northeast-2",
        "test-upload-bucket",
        "large.bin",
        large,
        part_size,
        1,
        progress.append,
        lambda: False,
    )
    assert progress == [part_size, 3]
    assert all(len(call.kwargs["Body"]) <= part_size for call in client.upload_part.call_args_list)
    client.complete_multipart_upload.assert_called_once()
    client.abort_multipart_upload.assert_not_called()


def test_cancel_after_last_part_aborts_instead_of_completing(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"123456")
    client = Mock()
    client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
    client.upload_part.return_value = {"ETag": '"one"'}
    checks = iter((False, True))
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)

    with pytest.raises(S3TransferError, match="s3.upload.cancelled"):
        gateway.multipart_file(
            _credentials(),
            "ap-northeast-2",
            "test-upload-bucket",
            "large.bin",
            source,
            5 * 1024 * 1024,
            1,
            lambda _size: None,
            lambda: next(checks),
        )
    client.complete_multipart_upload.assert_not_called()
    client.abort_multipart_upload.assert_called_once()


def test_explicit_retry_after_aborted_multipart_uses_new_upload_id(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"123456")
    client = Mock()
    client.create_multipart_upload.side_effect = (
        {"UploadId": "failed-upload"},
        {"UploadId": "retry-upload"},
    )
    client.upload_part.side_effect = (
        RuntimeError("first transfer failed"),
        {"ETag": '"retry-etag"'},
    )
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)

    with pytest.raises(S3TransferError):
        gateway.multipart_file(
            _credentials(),
            "ap-northeast-2",
            "test-upload-bucket",
            "large.bin",
            source,
            5 * 1024 * 1024,
            1,
            lambda _size: None,
            lambda: False,
        )
    gateway.multipart_file(
        _credentials(),
        "ap-northeast-2",
        "test-upload-bucket",
        "large.bin",
        source,
        5 * 1024 * 1024,
        1,
        lambda _size: None,
        lambda: False,
    )

    assert client.create_multipart_upload.call_count == 2
    client.abort_multipart_upload.assert_called_once_with(
        Bucket="test-upload-bucket", Key="large.bin", UploadId="failed-upload"
    )
    client.complete_multipart_upload.assert_called_once()
    assert client.complete_multipart_upload.call_args.kwargs["UploadId"] == "retry-upload"


@pytest.mark.parametrize("overwrite", (False, True))
def test_download_commit_preserves_existing_file_without_consent(tmp_path: Path, overwrite) -> None:
    destination = tmp_path / "file.txt"
    destination.write_bytes(b"original")
    client = Mock()
    client.download_file.side_effect = lambda _bucket, _key, name: Path(name).write_bytes(b"new")
    gateway = Boto3S3Gateway(lambda _credentials, _region: client)
    if overwrite:
        gateway.download_file(
            _credentials(), "ap-northeast-2", "test-bucket", "file.txt", destination, overwrite=True
        )
        assert destination.read_bytes() == b"new"
    else:
        with pytest.raises(S3TransferError, match="s3.download.destination.exists"):
            gateway.download_file(
                _credentials(), "ap-northeast-2", "test-bucket", "file.txt", destination
            )
        assert destination.read_bytes() == b"original"
    assert list(tmp_path.glob(".aws-connect-*.download")) == []
