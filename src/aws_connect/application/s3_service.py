"""S3 location, listing, preflight and bounded upload use cases."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
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


class UploadConflictPolicy(StrEnum):
    OVERWRITE = "overwrite"
    SKIP_EXISTING = "skip_existing"


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
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DownloadItem:
    bucket: str
    key: str
    destination: Path
    size: int

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    profile_id: int
    region: str
    items: tuple[DownloadItem, ...]


@dataclass(frozen=True, slots=True)
class DownloadSummary:
    downloaded: tuple[Path, ...]
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
        source_paths: set[Path] = set()
        for candidate in sources:
            expanded = candidate.expanduser()
            if expanded.is_symlink():
                raise ConfigurationError(
                    "s3.upload.source.symlink",
                    f"Symbolic links cannot be uploaded: {expanded.name}",
                )
            source = expanded.resolve()
            candidates: tuple[tuple[Path, PurePosixPath], ...]
            if source.is_file():
                candidates = ((source, PurePosixPath(source.name)),)
            elif source.is_dir():
                try:
                    descendants = tuple(sorted(source.rglob("*")))
                except OSError as error:
                    raise ConfigurationError(
                        "s3.upload.source.unreadable",
                        f"Could not inspect upload folder: {source.name}",
                    ) from error
                for descendant in descendants:
                    if descendant.is_symlink():
                        raise ConfigurationError(
                            "s3.upload.source.symlink",
                            f"Symbolic links cannot be uploaded: {descendant.name}",
                        )
                candidates = tuple(
                    (descendant.resolve(), PurePosixPath(descendant.relative_to(source).as_posix()))
                    for descendant in descendants
                    if descendant.is_file()
                )
            else:
                raise ConfigurationError(
                    "s3.upload.source.invalid", f"Not a readable file or folder: {source.name}"
                )
            for upload_source, relative_key in candidates:
                if upload_source in source_paths:
                    continue
                key = str(
                    PurePosixPath(location.prefix) / relative_key
                    if location.prefix
                    else relative_key
                )
                if key.startswith("/") or ".." in PurePosixPath(key).parts:
                    raise ConfigurationError(
                        "s3.upload.target.invalid",
                        "Upload target must stay below the selected prefix",
                    )
                if key in keys:
                    raise ConfigurationError(
                        "s3.upload.target.duplicate",
                        "Two local files resolve to the same target object key",
                    )
                try:
                    size = upload_source.stat().st_size
                except OSError as error:
                    raise ConfigurationError(
                        "s3.upload.source.unreadable",
                        f"Could not read upload file: {upload_source.name}",
                    ) from error
                source_paths.add(upload_source)
                keys.add(key)
                items.append(
                    UploadItem(
                        upload_source,
                        location.bucket,
                        key,
                        size,
                        self._gateway.object_exists(
                            credentials, selected.region, location.bucket, key
                        ),
                    )
                )
        if not items:
            raise ConfigurationError(
                "s3.upload.source.required", "At least one source file is required"
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

    def prepare_download(
        self,
        selected_objects: Sequence[S3Object],
        destination_root: Path,
        bucket: str,
        profile: str | int | None = None,
    ) -> DownloadPlan:
        selected = self._profiles.resolve(profile)
        credentials = self._sessions.require_credentials(selected.require_id())
        location = S3Location(None, selected.require_id(), "direct", bucket, "")
        root = destination_root.expanduser().resolve()
        if not selected_objects:
            raise ConfigurationError(
                "s3.download.selection.required", "Select at least one S3 file or folder"
            )
        if not root.is_dir():
            raise ConfigurationError(
                "s3.download.destination.invalid", "Download directory does not exist"
            )

        expanded: list[S3Object] = []
        for selected_object in selected_objects:
            if selected_object.is_prefix or selected_object.key.endswith("/"):
                expanded.extend(
                    self._gateway.list_objects_recursive(
                        credentials,
                        selected.region,
                        location.bucket,
                        selected_object.key,
                    )
                )
            else:
                expanded.append(selected_object)

        items: list[DownloadItem] = []
        keys: set[str] = set()
        destinations: set[Path] = set()
        for candidate in expanded:
            key = candidate.key.strip()
            if candidate.is_prefix or not key or key.endswith("/") or key in keys:
                continue
            relative = PurePosixPath(key)
            if relative.is_absolute() or ".." in relative.parts or "\\" in key:
                raise ConfigurationError(
                    "s3.download.key.invalid", "S3 object key cannot escape the destination folder"
                )
            target = root.joinpath(*relative.parts).resolve()
            if not target.is_relative_to(root):
                raise ConfigurationError(
                    "s3.download.key.invalid", "S3 object key cannot escape the destination folder"
                )
            if target in destinations:
                raise ConfigurationError(
                    "s3.download.destination.duplicate",
                    "Two S3 objects resolve to the same local destination",
                )
            if target.exists():
                raise ConfigurationError(
                    "s3.download.destination.exists",
                    f"A local file or folder already exists: {target.name}",
                )
            keys.add(key)
            destinations.add(target)
            items.append(DownloadItem(location.bucket, key, target, candidate.size))
        if not items:
            raise ConfigurationError(
                "s3.download.selection.empty",
                "The selected S3 folders contain no downloadable files",
            )
        return DownloadPlan(selected.require_id(), selected.region, tuple(items))

    def download(self, plan: DownloadPlan, context: OperationContext) -> DownloadSummary:
        """Download a prepared batch serially with shared progress and cancellation."""

        context.raise_if_cancelled()
        credentials = self._sessions.require_credentials(plan.profile_id)
        total = sum(item.size for item in plan.items)
        completed = 0
        downloaded: list[Path] = []
        current: DownloadItem | None = None

        def report_progress(delta: int) -> None:
            nonlocal completed
            completed += delta
            context.report(
                "downloading",
                "s3.download.progress",
                completed=completed,
                total=total,
                target=current.uri if current is not None else None,
            )

        context.report("starting", "s3.download.started", completed=0, total=total)
        try:
            for current in plan.items:
                context.raise_if_cancelled()
                try:
                    current.destination.parent.mkdir(parents=True, exist_ok=True)
                except OSError as error:
                    raise ConfigurationError(
                        "s3.download.destination.unavailable",
                        f"Could not create download folder for {current.destination.name}",
                    ) from error
                self._gateway.download_file(
                    credentials,
                    plan.region,
                    current.bucket,
                    current.key,
                    current.destination,
                    report_progress,
                    lambda: context.cancellation.is_cancellation_requested,
                )
                downloaded.append(current.destination)
            context.raise_if_cancelled()
        except ApplicationError as error:
            context.report(
                "failed",
                error.message_code,
                completed=completed,
                total=total,
                target=current.uri if current is not None else None,
            )
            if error.message_code == "s3.download.cancelled":
                raise OperationCancelled from error
            raise
        context.report("completed", "s3.download.completed", completed=total, total=total)
        return DownloadSummary(tuple(downloaded), completed)

    def upload(
        self,
        plan: UploadPlan,
        *,
        overwrite: bool | None = None,
        policy: UploadConflictPolicy | None = None,
        context: OperationContext,
    ) -> UploadSummary:
        """Upload one plan inside the caller-owned long-operation context."""

        context.raise_if_cancelled()
        conflict_policy = _resolve_upload_policy(overwrite, policy)
        if conflict_policy not in (
            UploadConflictPolicy.OVERWRITE,
            UploadConflictPolicy.SKIP_EXISTING,
        ):
            raise ConfigurationError(
                "s3.upload.conflict_policy.invalid", "Upload conflict policy is invalid"
            )
        if conflict_policy is UploadConflictPolicy.OVERWRITE:
            transfer_items = plan.items
            skipped: list[str] = []
        else:
            transfer_items = tuple(item for item in plan.items if not item.exists)
            skipped = [item.uri for item in plan.items if item.exists]
        credentials = self._sessions.require_credentials(plan.profile_id)
        total = sum(item.size for item in transfer_items)
        completed = 0
        uploaded: list[str] = []

        if not transfer_items:
            context.report("completed", "s3.upload.completed", completed=0, total=0)
            return UploadSummary((), 0, tuple(skipped))

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
            for item in transfer_items:
                context.raise_if_cancelled()
                if (
                    conflict_policy is UploadConflictPolicy.SKIP_EXISTING
                    and self._gateway.object_exists(credentials, plan.region, item.bucket, item.key)
                ):
                    skipped.append(item.uri)
                    total -= item.size
                    continue
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
        return UploadSummary(tuple(uploaded), completed, tuple(skipped))


def _resolve_upload_policy(
    overwrite: bool | None, policy: UploadConflictPolicy | None
) -> UploadConflictPolicy:
    if policy is not None and overwrite is not None:
        compatible = (
            UploadConflictPolicy.OVERWRITE if overwrite else UploadConflictPolicy.SKIP_EXISTING
        )
        if policy is not compatible:
            raise ConfigurationError(
                "s3.upload.conflict_policy.conflicting",
                "Specify either overwrite or a matching upload conflict policy",
            )
    if policy is not None:
        return policy
    if overwrite is not None:
        return UploadConflictPolicy.OVERWRITE if overwrite else UploadConflictPolicy.SKIP_EXISTING
    raise ConfigurationError(
        "s3.upload.conflict_policy.required", "Select how existing S3 objects should be handled"
    )
