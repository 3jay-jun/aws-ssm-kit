"""S3 location, listing, preflight and bounded upload use cases."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from aws_connect.application.authentication_service import SessionGuard
from aws_connect.application.operations import (
    OperationCancelled,
    OperationContext,
)
from aws_connect.application.ports import S3Gateway, S3LocationStore, S3Object
from aws_connect.application.profile_service import ProfileService
from aws_connect.domain.errors import (
    ApplicationError,
    ConfigurationError,
)
from aws_connect.domain.s3_location import S3Location

MULTIPART_THRESHOLD = 16 * 1024 * 1024
MULTIPART_PART_SIZE = 8 * 1024 * 1024
MAX_CONCURRENCY = 1


@dataclass(frozen=True, slots=True)
class SaveS3LocationRequest:
    profile: str | int | None
    name: str
    bucket: str
    prefix: str = ""
    location_id: int | None = None


@dataclass(frozen=True, slots=True)
class UploadItem:
    source: Path
    bucket: str
    key: str
    size: int
    exists: bool

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


@dataclass(frozen=True, slots=True)
class UploadPlan:
    profile_id: int
    region: str
    items: tuple[UploadItem, ...]


@dataclass(frozen=True, slots=True)
class UploadSummary:
    uploaded: tuple[str, ...]
    bytes_transferred: int


class S3LocationService:
    def __init__(self, profiles: ProfileService, store: S3LocationStore) -> None:
        self._profiles = profiles
        self._store = store

    def list(self, profile: str | int | None = None) -> list[S3Location]:
        return self._store.list_s3_locations(self._profiles.resolve(profile).require_id())

    def show(self, selector: str | int, profile: str | int | None = None) -> S3Location:
        selected = self._profiles.resolve(profile)
        value = (
            self._store.get_s3_location(selector)
            if isinstance(selector, int)
            else self._store.get_s3_location_by_name(selected.require_id(), selector)
        )
        if value is None or value.profile_id != selected.require_id():
            raise ConfigurationError("s3.location.not_found", "Saved S3 location was not found")
        return value

    def create(self, request: SaveS3LocationRequest) -> S3Location:
        profile = self._profiles.resolve(request.profile)
        return self._store.create_s3_location(
            S3Location(None, profile.require_id(), request.name, request.bucket, request.prefix)
        )

    def update(self, request: SaveS3LocationRequest) -> S3Location:
        if request.location_id is None:
            raise ConfigurationError("s3.location.id.required", "Location id is required")
        current = self.show(request.location_id, request.profile)
        return self._store.update_s3_location(
            S3Location(
                request.location_id,
                current.profile_id,
                request.name,
                request.bucket,
                request.prefix,
            )
        )

    def delete(self, selector: str | int, profile: str | int | None = None) -> None:
        self._store.delete_s3_location(self.show(selector, profile).require_id())


class S3Service:
    def __init__(
        self, profiles: ProfileService, sessions: SessionGuard, gateway: S3Gateway
    ) -> None:
        self._profiles = profiles
        self._sessions = sessions
        self._gateway = gateway

    def list_buckets(self, profile: str | int | None = None) -> list[str]:
        """Return the optional catalog; direct Bucket input never depends on it."""

        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        return self._gateway.list_buckets(credentials, selected.region)

    def list_objects(
        self, bucket: str, prefix: str = "", profile: str | int | None = None
    ) -> list[S3Object]:
        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        location = S3Location(None, selected.require_id(), "direct", bucket, prefix)
        return self._gateway.list_objects(
            credentials, selected.region, location.bucket, location.prefix
        )

    def prepare_upload(
        self,
        sources: Sequence[Path],
        bucket: str,
        prefix: str = "",
        profile: str | int | None = None,
    ) -> UploadPlan:
        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        location = S3Location(None, selected.require_id(), "direct", bucket, prefix)
        if not sources:
            raise ConfigurationError(
                "s3.upload.source.required", "At least one source file is required"
            )
        items: list[UploadItem] = []
        keys: set[str] = set()
        for candidate in sources:
            source = candidate.expanduser().resolve()
            if not source.is_file():
                raise ConfigurationError(
                    "s3.upload.source.invalid", f"Not a readable file: {source.name}"
                )
            key = (
                str(PurePosixPath(location.prefix) / source.name)
                if location.prefix
                else source.name
            )
            if key in keys:
                raise ConfigurationError(
                    "s3.upload.target.duplicate",
                    "Two local files resolve to the same target object key",
                )
            keys.add(key)
            items.append(
                UploadItem(
                    source,
                    location.bucket,
                    key,
                    source.stat().st_size,
                    self._gateway.object_exists(credentials, selected.region, location.bucket, key),
                )
            )
        return UploadPlan(selected.require_id(), selected.region, tuple(items))

    def delete_object(
        self,
        bucket: str,
        key: str,
        profile: str | int | None = None,
    ) -> None:
        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        location = S3Location(None, selected.require_id(), "direct", bucket, "")
        normalized_key = key.strip()
        if not normalized_key or normalized_key.endswith("/"):
            raise ConfigurationError("s3.delete.object.required", "Select one non-prefix S3 object")
        self._gateway.delete_object(credentials, selected.region, location.bucket, normalized_key)

    def download_object(
        self,
        bucket: str,
        key: str,
        destination: Path,
        profile: str | int | None = None,
    ) -> Path:
        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        location = S3Location(None, selected.require_id(), "direct", bucket, "")
        normalized_key = key.strip()
        target = destination.expanduser().resolve()
        if not normalized_key or normalized_key.endswith("/"):
            raise ConfigurationError("s3.download.object.required", "Select one S3 object")
        if not target.parent.is_dir():
            raise ConfigurationError(
                "s3.download.destination.invalid", "Download directory does not exist"
            )
        self._gateway.download_file(
            credentials, selected.region, location.bucket, normalized_key, target
        )
        return target

    def upload(
        self,
        plan: UploadPlan,
        *,
        overwrite: bool,
        context: OperationContext,
    ) -> UploadSummary:
        """Upload one plan inside the caller-owned long-operation context."""

        context.raise_if_cancelled()
        if any(item.exists for item in plan.items) and not overwrite:
            raise ConfigurationError(
                "s3.upload.overwrite_confirmation_required",
                "Existing objects require explicit overwrite confirmation",
            )
        credentials = self._sessions.require_credentials(plan.profile_id)
        newly_existing = any(
            not item.exists
            and self._gateway.object_exists(credentials, plan.region, item.bucket, item.key)
            for item in plan.items
        )
        if newly_existing and not overwrite:
            raise ConfigurationError(
                "s3.upload.overwrite_confirmation_required",
                "An object appeared after preflight; refresh the plan and confirm overwrite",
            )
        total = sum(item.size for item in plan.items)
        completed = 0
        uploaded: list[str] = []

        def progress_for(item: UploadItem) -> Callable[[int], None]:
            def emit(delta: int) -> None:
                nonlocal completed
                completed += delta
                context.report(
                    "uploading",
                    "s3.upload.progress",
                    completed=completed,
                    total=total,
                    target=item.uri,
                )

            return emit

        context.report("starting", "s3.upload.started", completed=0, total=total)
        try:
            for item in plan.items:
                context.raise_if_cancelled()
                if item.size >= MULTIPART_THRESHOLD:
                    self._gateway.multipart_file(
                        credentials,
                        plan.region,
                        item.bucket,
                        item.key,
                        item.source,
                        MULTIPART_PART_SIZE,
                        MAX_CONCURRENCY,
                        progress_for(item),
                        lambda: context.cancellation.is_cancellation_requested,
                    )
                else:
                    self._gateway.put_file(
                        credentials,
                        plan.region,
                        item.bucket,
                        item.key,
                        item.source,
                        progress_for(item),
                        lambda: context.cancellation.is_cancellation_requested,
                    )
                uploaded.append(item.uri)
            context.raise_if_cancelled()
        except ApplicationError as error:
            if error.message_code == "s3.upload.cancelled":
                raise OperationCancelled from error
            raise
        context.report("completed", "s3.upload.completed", completed=total, total=total)
        return UploadSummary(tuple(uploaded), completed)
