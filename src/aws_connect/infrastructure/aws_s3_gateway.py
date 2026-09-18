"""Explicit-credential S3 adapter with deterministic multipart cleanup."""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from aws_connect.application.ports import S3Object
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import ApplicationError, S3TransferError
from aws_connect.infrastructure.aws_diagnostics import observed_client
from aws_connect.infrastructure.aws_identity_gateway import translate_aws_error

ClientFactory = Callable[[PlainCredentials, str], Any]


class _Cancelled(Exception):
    pass


class _ProgressReader:
    def __init__(
        self, raw: BinaryIO, progress: Callable[[int], None], cancelled: Callable[[], bool]
    ) -> None:
        self._raw = raw
        self._progress = progress
        self._cancelled = cancelled

    def read(self, size: int = -1) -> bytes:
        if self._cancelled():
            raise _Cancelled
        value = self._raw.read(size)
        if value:
            self._progress(len(value))
        return value


class Boto3S3Gateway:
    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self._client_factory = client_factory or _client

    def list_buckets(self, credentials: PlainCredentials, region: str) -> list[str]:
        client = self._client_factory(credentials, region)
        try:
            response = client.list_buckets()
            return sorted(
                str(item["Name"]) for item in response.get("Buckets", []) if item.get("Name")
            )
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="s3", action="ListBuckets") from error

    def list_objects(
        self, credentials: PlainCredentials, region: str, bucket: str, prefix: str
    ) -> list[S3Object]:
        return self._list_objects(credentials, region, bucket, prefix, delimiter="/")

    def list_objects_recursive(
        self, credentials: PlainCredentials, region: str, bucket: str, prefix: str
    ) -> list[S3Object]:
        """List every object below a prefix without treating child prefixes as rows."""

        return self._list_objects(credentials, region, bucket, prefix, delimiter=None)

    def _list_objects(
        self,
        credentials: PlainCredentials,
        region: str,
        bucket: str,
        prefix: str,
        *,
        delimiter: str | None,
    ) -> list[S3Object]:
        client = self._client_factory(credentials, region)
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if delimiter is not None:
            request["Delimiter"] = delimiter
        result: list[S3Object] = []
        try:
            while True:
                response = client.list_objects_v2(**request)
                result.extend(
                    S3Object(str(item["Prefix"]), 0, None, True)
                    for item in response.get("CommonPrefixes", [])
                )
                result.extend(
                    S3Object(
                        str(item["Key"]),
                        int(item.get("Size", 0)),
                        _datetime(item.get("LastModified")),
                        str(item.get("Key", "")).endswith("/"),
                    )
                    for item in response.get("Contents", [])
                    if str(item.get("Key", "")) != prefix
                )
                token = response.get("NextContinuationToken")
                if not isinstance(token, str):
                    return result
                request["ContinuationToken"] = token
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="s3", action="ListObjectsV2") from error

    def object_exists(
        self, credentials: PlainCredentials, region: str, bucket: str, key: str
    ) -> bool:
        client = self._client_factory(credentials, region)
        try:
            response = client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
            return any(str(item.get("Key", "")) == key for item in response.get("Contents", []))
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="s3", action="ListObjectsV2") from error

    def delete_object(
        self, credentials: PlainCredentials, region: str, bucket: str, key: str
    ) -> None:
        client = self._client_factory(credentials, region)
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="s3", action="DeleteObject") from error

    def rename_object(
        self, credentials: PlainCredentials, region: str, bucket: str, key: str, target: str
    ) -> None:
        """Conditional server-side copy, then delete only the observed source revision."""
        client = self._client_factory(credentials, region)
        try:
            source = client.head_object(Bucket=bucket, Key=key)
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="s3", action="HeadObject") from error
        if int(source["ContentLength"]) > 5 * 1024**3:
            raise S3TransferError(
                "s3.rename.too_large",
                "5 GiB 초과 파일은 이름 변경을 지원하지 않습니다. 원본은 유지됩니다.",
            )
        try:
            client.copy_object(
                Bucket=bucket,
                Key=target,
                CopySource={"Bucket": bucket, "Key": key},
                CopySourceIfMatch=source["ETag"],
                IfNoneMatch="*",
            )
        except (ClientError, BotoCoreError) as error:
            if isinstance(error, ClientError) and error.response.get("Error", {}).get("Code") in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
            }:
                raise S3TransferError(
                    "s3.rename.changed",
                    "대상 파일이 생겼거나 원본이 변경되었습니다. "
                    "목록을 새로고침하고 다시 확인하세요.",
                ) from error
            raise translate_aws_error(error, service="s3", action="CopyObject") from error
        try:
            client.delete_object(Bucket=bucket, Key=key, IfMatch=source["ETag"])
        except (ClientError, BotoCoreError) as error:
            raise S3TransferError(
                "s3.rename.partial",
                "새 이름으로 복사했지만 원본을 삭제하지 못했습니다. "
                "목록을 새로고침해 두 파일을 확인하세요.",
                aws_service="s3",
                aws_action="DeleteObject",
            ) from error

    def download_file(
        self,
        credentials: PlainCredentials,
        region: str,
        bucket: str,
        key: str,
        destination: Path,
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        *,
        overwrite: bool = False,
    ) -> None:
        client = self._client_factory(credentials, region)
        temporary: Path | None = None
        report_progress = progress or (lambda _delta: None)
        is_cancelled = cancelled or (lambda: False)

        def transferred(delta: int) -> None:
            if is_cancelled():
                raise _Cancelled
            report_progress(delta)

        try:
            if is_cancelled():
                raise _Cancelled
            with tempfile.NamedTemporaryFile(
                prefix=".aws-connect-", suffix=".download", dir=destination.parent, delete=False
            ) as raw:
                temporary = Path(raw.name)
            if progress is None and cancelled is None:
                client.download_file(bucket, key, str(temporary))
            else:
                client.download_file(bucket, key, str(temporary), Callback=transferred)
            if is_cancelled():
                raise _Cancelled
            if overwrite:
                temporary.replace(destination)
            elif os.name == "nt":
                # Windows rename fails atomically when the destination already exists.
                temporary.rename(destination)
            else:
                os.link(temporary, destination)
                temporary.unlink()
            temporary = None
        except (_Cancelled, KeyboardInterrupt) as error:
            raise S3TransferError(
                "s3.download.cancelled",
                "Download cancelled before GetObject completed",
                aws_service="s3",
                aws_action="GetObject",
            ) from error
        except FileExistsError as error:
            raise S3TransferError(
                "s3.download.destination.exists",
                "Destination appeared before download completed; retry to confirm overwrite",
                aws_service="s3",
                aws_action="GetObject",
            ) from error
        except Exception as error:
            raise _translate_transfer(error, "GetObject") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def put_file(
        self,
        credentials: PlainCredentials,
        region: str,
        bucket: str,
        key: str,
        source: Path,
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
    ) -> None:
        client = self._client_factory(credentials, region)
        try:
            with source.open("rb") as raw:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=_ProgressReader(raw, progress, cancelled),
                    ContentLength=source.stat().st_size,
                )
        except (_Cancelled, KeyboardInterrupt) as error:
            raise S3TransferError(
                "s3.upload.cancelled",
                "Upload cancelled before PutObject completed",
                aws_service="s3",
                aws_action="PutObject",
            ) from error
        except Exception as error:
            raise _translate_transfer(error, "PutObject") from error

    def multipart_file(
        self,
        credentials: PlainCredentials,
        region: str,
        bucket: str,
        key: str,
        source: Path,
        part_size: int,
        max_concurrency: int,
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
    ) -> None:
        if part_size < 5 * 1024 * 1024 or not 1 <= max_concurrency <= 8:
            raise S3TransferError(
                "s3.multipart.configuration.invalid", "Multipart bounds are invalid"
            )
        client = self._client_factory(credentials, region)
        upload_id: str | None = None
        action = "CreateMultipartUpload"
        try:
            created = client.create_multipart_upload(Bucket=bucket, Key=key)
            upload_id = str(created["UploadId"])
            parts: list[dict[str, Any]] = []
            with source.open("rb") as raw:
                for part_number in range(1, math.ceil(source.stat().st_size / part_size) + 1):
                    if cancelled():
                        raise _Cancelled
                    body = raw.read(part_size)
                    action = "UploadPart"
                    response = client.upload_part(
                        Bucket=bucket,
                        Key=key,
                        UploadId=upload_id,
                        PartNumber=part_number,
                        Body=body,
                    )
                    parts.append({"ETag": str(response["ETag"]), "PartNumber": part_number})
                    progress(len(body))
            if cancelled():
                raise _Cancelled
            action = "CompleteMultipartUpload"
            client.complete_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
            )
        except (Exception, KeyboardInterrupt) as error:
            translated: ApplicationError = (
                S3TransferError(
                    "s3.upload.cancelled",
                    "Multipart upload cancelled",
                    aws_service="s3",
                    aws_action=action,
                )
                if isinstance(error, (_Cancelled, KeyboardInterrupt))
                else _translate_transfer(error, action)
            )
            if upload_id is not None:
                try:
                    client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
                except Exception as cleanup_error:
                    cleanup = type(cleanup_error).__name__
                    translated.technical_cause = (
                        f"{translated.technical_cause}; AbortMultipartUpload failed: {cleanup}"
                    )
            raise translated from error


def _translate_transfer(error: Exception, action: str) -> ApplicationError:
    if isinstance(error, (ClientError, BotoCoreError)):
        return translate_aws_error(error, service="s3", action=action)
    return S3TransferError(
        "s3.transfer.failed",
        type(error).__name__,
        retryable=True,
        aws_service="s3",
        aws_action=action,
    )


def _datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _client(credentials: PlainCredentials, region: str) -> Any:
    return observed_client(
        boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_session_token=credentials.session_token,
        )
    )
