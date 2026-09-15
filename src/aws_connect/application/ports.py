"""Narrow ports required by profile and authentication use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from aws_connect.domain.app_settings import AppSettings
from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials, SessionCredentials
from aws_connect.domain.s3_location import S3Location
from aws_connect.domain.saved_secret import SavedSecret
from aws_connect.domain.tunnel_session import TunnelSession

if TYPE_CHECKING:
    from aws_connect.application.activity_log_service import ActivityEvent, ManagedLogEntry


@dataclass(frozen=True, slots=True)
class AwsIdentity:
    account_id: str
    user_id: str
    arn: str


@dataclass(frozen=True, slots=True, repr=False)
class IssuedSession:
    credentials: PlainCredentials
    expires_at_utc: datetime


class ProfileStore(Protocol):
    def list(self) -> list[AwsProfile]: ...
    def get(self, profile_id: int) -> AwsProfile | None: ...
    def get_by_name(self, name: str) -> AwsProfile | None: ...
    def create(self, profile: AwsProfile) -> AwsProfile: ...
    def update(self, profile: AwsProfile) -> AwsProfile: ...
    def delete(self, profile_id: int) -> None: ...
    def set_default(self, profile_id: int) -> AwsProfile: ...
    def get_default(self) -> AwsProfile | None: ...
    def get_session(self, profile_id: int) -> SessionCredentials | None: ...
    def put_session(self, session: SessionCredentials) -> None: ...
    def delete_session(self, profile_id: int) -> None: ...


class TunnelSessionStore(Protocol):
    def list_tunnels(self, profile_id: int) -> list[TunnelSession]: ...
    def get_tunnel(self, tunnel_id: int) -> TunnelSession | None: ...
    def get_tunnel_by_name(self, profile_id: int, name: str) -> TunnelSession | None: ...
    def create_tunnel(self, session: TunnelSession) -> TunnelSession: ...
    def update_tunnel(self, session: TunnelSession) -> TunnelSession: ...
    def delete_tunnel(self, tunnel_id: int) -> None: ...
    def touch_tunnel(self, tunnel_id: int, used_at: datetime) -> None: ...


class CredentialProtector(Protocol):
    def protect(self, value: str) -> bytes: ...
    def unprotect(self, value: bytes) -> str: ...


class IdentityGateway(Protocol):
    def get_identity(self, credentials: PlainCredentials, region: str) -> AwsIdentity: ...

    def get_session_token(
        self,
        credentials: PlainCredentials,
        region: str,
        mfa_arn: str | None,
        mfa_code: str | None,
    ) -> IssuedSession: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class ManagedInstance:
    instance_id: str
    ping_status: str
    ip_address: str | None
    platform_name: str | None


@dataclass(frozen=True, slots=True)
class Ec2Metadata:
    instance_id: str
    name: str | None
    private_ip_address: str | None


@dataclass(frozen=True, slots=True, repr=False)
class StartedSsmSession:
    session_id: str
    stream_url: str
    token_value: str


@dataclass(frozen=True, slots=True)
class PluginDiagnostic:
    path: Path
    exists: bool
    version: str | None
    supported: bool
    environment_ready: bool


@dataclass(frozen=True, slots=True, repr=False)
class PluginInvocation:
    session: StartedSsmSession
    region: str
    target: str
    document_name: str | None = None
    parameters: Mapping[str, Sequence[str]] | None = None


class ManagedInstanceGateway(Protocol):
    def list_online(self, credentials: PlainCredentials, region: str) -> list[ManagedInstance]: ...

    def list_managed(self, credentials: PlainCredentials, region: str) -> list[ManagedInstance]: ...

    def start_session(
        self,
        credentials: PlainCredentials,
        region: str,
        target: str,
        document_name: str | None = None,
        parameters: Mapping[str, Sequence[str]] | None = None,
    ) -> StartedSsmSession: ...

    def end_session(self, credentials: PlainCredentials, region: str, session_id: str) -> None: ...


class Ec2MetadataGateway(Protocol):
    def describe(
        self, credentials: PlainCredentials, region: str, instance_ids: Sequence[str]
    ) -> dict[str, Ec2Metadata]: ...


@dataclass(frozen=True, slots=True)
class Ec2Instance:
    instance_id: str
    state: str
    name: str | None
    private_ip_address: str | None
    platform_name: str | None


class Ec2InventoryGateway(Protocol):
    def list_inventory(self, credentials: PlainCredentials, region: str) -> list[Ec2Instance]: ...


class Ec2PowerGateway(Protocol):
    def start_instance(
        self, credentials: PlainCredentials, region: str, instance_id: str
    ) -> None: ...

    def reboot_instance(
        self, credentials: PlainCredentials, region: str, instance_id: str
    ) -> None: ...


class Ec2FavoriteStore(Protocol):
    def list_ec2_favorites(self, profile_id: int, region: str) -> set[str]: ...

    def set_ec2_favorite(
        self, profile_id: int, region: str, instance_id: str, favorite: bool
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RdsEndpoint:
    identifier: str
    host: str
    port: int
    engine: str


class RdsEndpointGateway(Protocol):
    def list_endpoints(self, credentials: PlainCredentials, region: str) -> list[RdsEndpoint]: ...


class SessionPlugin(Protocol):
    def diagnose(self) -> PluginDiagnostic: ...
    def run(self, invocation: PluginInvocation) -> int: ...
    def launch(
        self, invocation: PluginInvocation, *, external_terminal: bool = False
    ) -> SessionProcess: ...


class SessionProcess(Protocol):
    """Minimal child-process contract used by managed SSM lifecycles."""

    @property
    def pid(self) -> int: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


class LocalPortChecker(Protocol):
    def is_available(self, port: int) -> bool: ...


class SettingsStore(Protocol):
    def get_setting(self, key: str) -> str | None: ...
    def put_setting(self, key: str, value: str) -> None: ...
    def delete_setting(self, key: str) -> None: ...
    def replace_settings(self, values: Mapping[str, str | None]) -> None: ...


class DiagnosticLogExporter(Protocol):
    def export(self, source_directory: Path, destination: Path) -> int: ...


class ManagedLogReader(Protocol):
    def read_recent(self, source_directory: Path, limit: int) -> list[ManagedLogEntry]: ...


class ActivityEventWriter(Protocol):
    def write(self, event: ActivityEvent) -> None: ...


class LoggingConfigurator(Protocol):
    def apply(self, settings: AppSettings) -> Path: ...


class PrivateFileAccess(Protocol):
    def restrict(self, path: Path) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class RetrievedSecret:
    """Raw SecretString held only for the duration of the requesting operation."""

    secret_id: str
    secret_string: str
    version_id: str | None = None
    version_stages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ListedSecret:
    """Non-value metadata returned by the optional ListSecrets operation."""

    name: str
    arn: str


class SecretsGateway(Protocol):
    def get_secret_value(
        self, credentials: PlainCredentials, region: str, secret_id: str
    ) -> RetrievedSecret: ...

    def list_secrets(self, credentials: PlainCredentials, region: str) -> list[ListedSecret]: ...

    def put_secret_value(
        self, credentials: PlainCredentials, region: str, secret_id: str, secret_string: str
    ) -> str | None: ...


class RemoteSecretCommandGateway(Protocol):
    """Run the one fixed Secret lookup command on a selected managed EC2 node."""

    def get_secret_value(
        self,
        credentials: PlainCredentials,
        region: str,
        instance_id: str,
        platform_name: str,
        secret_id: str,
        raise_if_cancelled: Callable[[], None],
    ) -> RetrievedSecret: ...


class S3LocationStore(Protocol):
    def list_s3_locations(self, profile_id: int) -> list[S3Location]: ...
    def get_s3_location(self, location_id: int) -> S3Location | None: ...
    def get_s3_location_by_name(self, profile_id: int, name: str) -> S3Location | None: ...
    def create_s3_location(self, location: S3Location) -> S3Location: ...
    def update_s3_location(self, location: S3Location) -> S3Location: ...
    def delete_s3_location(self, location_id: int) -> None: ...


class SavedSecretStore(Protocol):
    def list_saved_secrets(self, profile_id: int) -> list[SavedSecret]: ...
    def get_saved_secret(self, saved_secret_id: int) -> SavedSecret | None: ...
    def get_saved_secret_by_identifier(
        self, profile_id: int, identifier: str
    ) -> SavedSecret | None: ...
    def create_saved_secret(self, saved_secret: SavedSecret) -> SavedSecret: ...
    def update_saved_secret(self, saved_secret: SavedSecret) -> SavedSecret: ...
    def delete_saved_secret(self, saved_secret_id: int) -> None: ...


@dataclass(frozen=True, slots=True)
class S3Object:
    key: str
    size: int
    last_modified: datetime | None
    is_prefix: bool = False


class S3Gateway(Protocol):
    def list_buckets(self, credentials: PlainCredentials, region: str) -> list[str]: ...

    def list_objects(
        self, credentials: PlainCredentials, region: str, bucket: str, prefix: str
    ) -> list[S3Object]: ...
    def object_exists(
        self, credentials: PlainCredentials, region: str, bucket: str, key: str
    ) -> bool: ...
    def delete_object(
        self, credentials: PlainCredentials, region: str, bucket: str, key: str
    ) -> None: ...
    def download_file(
        self,
        credentials: PlainCredentials,
        region: str,
        bucket: str,
        key: str,
        destination: Path,
    ) -> None: ...
    def put_file(
        self,
        credentials: PlainCredentials,
        region: str,
        bucket: str,
        key: str,
        source: Path,
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
    ) -> None: ...
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
    ) -> None: ...
