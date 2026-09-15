"""Production composition root."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from aws_connect.application.activity_log_service import ActivityLogService
from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.authentication_service import (
    AuthenticationService,
    OperationCoordinator,
    SessionGuard,
)
from aws_connect.application.connection_lifecycle import ProfileConnectionLifecycleService
from aws_connect.application.ec2_service import Ec2Service
from aws_connect.application.legacy_import import LegacyIniImportService
from aws_connect.application.ports import SessionPlugin
from aws_connect.application.profile_service import ProfileService
from aws_connect.application.rds_endpoint_service import RdsEndpointService
from aws_connect.application.rds_tunnel_operation import RdsTunnelOperationCoordinator
from aws_connect.application.rds_tunnel_service import RdsTunnelService, TunnelSessionService
from aws_connect.application.s3_service import S3LocationService, S3Service
from aws_connect.application.secrets_service import SecretsService
from aws_connect.application.settings_service import DiagnosticLogService, SettingsService
from aws_connect.application.ssm_session import ForegroundSsmSessionRunner, ManagedSsmSessionRunner
from aws_connect.application.system_info import (
    DoctorService,
    StaticSystemInfoService,
    SystemInfoService,
)
from aws_connect.infrastructure.aws_identity_gateway import Boto3IdentityGateway
from aws_connect.infrastructure.aws_rds_gateway import Boto3RdsEndpointGateway
from aws_connect.infrastructure.aws_remote_secret_gateway import (
    Boto3RemoteSecretCommandGateway,
)
from aws_connect.infrastructure.aws_s3_gateway import Boto3S3Gateway
from aws_connect.infrastructure.aws_secrets_gateway import Boto3SecretsGateway
from aws_connect.infrastructure.aws_ssm_gateway import (
    Boto3Ec2MetadataGateway,
    Boto3ManagedInstanceGateway,
)
from aws_connect.infrastructure.clock import SystemClock
from aws_connect.infrastructure.data_protection import WindowsDpapiProtector
from aws_connect.infrastructure.diagnostic_logs import MaskedDiagnosticLogExporter
from aws_connect.infrastructure.local_port_checker import SocketLocalPortChecker
from aws_connect.infrastructure.logging_setup import RotatingLogConfigurator
from aws_connect.infrastructure.managed_logs import (
    MaskedManagedLogReader,
    StructuredActivityEventWriter,
)
from aws_connect.infrastructure.private_file_access import WindowsPrivateFileAccess
from aws_connect.infrastructure.runtime_diagnostics import LocalRuntimeDiagnosticProbe
from aws_connect.infrastructure.session_manager_plugin import SessionManagerPlugin
from aws_connect.infrastructure.sqlite_profile_store import SqliteProfileStore


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    profiles: ProfileService
    authentication: AuthenticationService
    operations: OperationCoordinator
    sessions: SessionGuard
    ec2: Ec2Service | None = None
    tunnel_sessions: TunnelSessionService | None = None
    rds_tunnels: RdsTunnelOperationCoordinator | None = None
    rds_endpoints: RdsEndpointService | None = None
    settings: SettingsService | None = None
    diagnostic_logs: DiagnosticLogService | None = None
    legacy_import: LegacyIniImportService | None = None
    doctor: DoctorService | None = None
    secrets: SecretsService | None = None
    s3_locations: S3LocationService | None = None
    s3: S3Service | None = None
    activity_logs: ActivityLogService | None = None
    authenticated_operations: AuthenticatedOperationCoordinator | None = None
    connection_lifecycle: ProfileConnectionLifecycleService | None = None


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    data: Path
    database: Path
    entropy: Path
    default_logs: Path


@dataclass(frozen=True, slots=True)
class _LocalFoundation:
    paths: RuntimePaths
    store: SqliteProfileStore
    protector: WindowsDpapiProtector
    clock: SystemClock
    plugin: SessionPlugin
    settings: SettingsService
    diagnostic_logs: DiagnosticLogService
    legacy_import: LegacyIniImportService
    doctor: DoctorService
    activity_logs: ActivityLogService


def build_system_info_service() -> SystemInfoService:
    """Compose the application service used by both presentation adapters."""

    return StaticSystemInfoService()


def user_data_directory() -> Path:
    """Return the accepted per-user writable data location."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is required on supported Windows systems")
    return Path(local_app_data) / "AWSConnect"


def application_directory() -> Path:
    """Return the directory that owns bundled runtime assets."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def build_session_plugin(root: Path | None = None) -> SessionPlugin:
    return SessionManagerPlugin((root or application_directory()) / "session-manager-plugin.exe")


def runtime_paths(data_directory: Path | None = None) -> RuntimePaths:
    root = (data_directory or user_data_directory()).expanduser().resolve()
    return RuntimePaths(root, root / "aws_connect.db", root / "install.entropy", root / "logs")


def build_doctor_service(data_directory: Path | None = None) -> DoctorService:
    return _build_local_foundation(data_directory).doctor


def _build_local_foundation(data_directory: Path | None = None) -> _LocalFoundation:
    paths = runtime_paths(data_directory)
    private_file_access = WindowsPrivateFileAccess()
    store = SqliteProfileStore(paths.database, private_file_access)
    protector = WindowsDpapiProtector(paths.entropy, private_file_access)
    clock = SystemClock()
    plugin = build_session_plugin()
    settings = SettingsService(
        store,
        paths.default_logs,
        RotatingLogConfigurator(private_file_access),
    )
    settings.apply_current()
    diagnostic_logs = DiagnosticLogService(
        settings,
        MaskedDiagnosticLogExporter(private_file_access),
    )
    activity_logs = ActivityLogService(
        settings,
        MaskedManagedLogReader(),
        StructuredActivityEventWriter(),
        clock,
    )
    legacy_import = LegacyIniImportService(store, store, protector)
    doctor = DoctorService(LocalRuntimeDiagnosticProbe(store, protector, plugin, settings, clock))
    return _LocalFoundation(
        paths,
        store,
        protector,
        clock,
        plugin,
        settings,
        diagnostic_logs,
        legacy_import,
        doctor,
        activity_logs,
    )


def build_application_services(data_directory: Path | None = None) -> ApplicationServices:
    """Compose all real Phase 1 adapters in one place."""

    local = _build_local_foundation(data_directory)
    store = local.store
    protector = local.protector
    gateway = Boto3IdentityGateway()
    clock = local.clock
    profiles = ProfileService(store, protector, gateway)
    authentication = AuthenticationService(profiles, store, protector, gateway, clock)
    sessions = authentication.session_guard
    managed_instances = Boto3ManagedInstanceGateway()
    metadata = Boto3Ec2MetadataGateway()
    plugin = local.plugin
    runner = ForegroundSsmSessionRunner(managed_instances, plugin)
    managed_runner = ManagedSsmSessionRunner(managed_instances, plugin)
    ec2 = Ec2Service(
        profiles,
        sessions,
        managed_instances,
        metadata,
        plugin,
        managed_runner,
        inventory=metadata,
        power=metadata,
        favorites=store,
    )
    secrets = SecretsService(
        profiles,
        sessions,
        Boto3SecretsGateway(),
        managed_instances=managed_instances,
        remote_gateway=Boto3RemoteSecretCommandGateway(),
        metadata=metadata,
        saved=store,
    )
    s3_locations = S3LocationService(profiles, store)
    s3 = S3Service(profiles, sessions, Boto3S3Gateway())
    tunnel_sessions = TunnelSessionService(profiles, store)
    rds_endpoints = RdsEndpointService(profiles, sessions, Boto3RdsEndpointGateway())
    tunnel_service = RdsTunnelService(
        profiles,
        sessions,
        tunnel_sessions,
        store,
        managed_instances,
        runner,
        SocketLocalPortChecker(),
        clock,
        managed_runner,
    )
    operations = OperationCoordinator(authentication, clock)
    authenticated_operations = AuthenticatedOperationCoordinator(operations, clock)
    rds_tunnels = RdsTunnelOperationCoordinator(tunnel_service, authenticated_operations)
    return ApplicationServices(
        profiles=profiles,
        authentication=authentication,
        operations=operations,
        sessions=sessions,
        authenticated_operations=authenticated_operations,
        ec2=ec2,
        tunnel_sessions=tunnel_sessions,
        rds_tunnels=rds_tunnels,
        rds_endpoints=rds_endpoints,
        settings=local.settings,
        diagnostic_logs=local.diagnostic_logs,
        legacy_import=local.legacy_import,
        doctor=local.doctor,
        secrets=secrets,
        s3_locations=s3_locations,
        s3=s3,
        activity_logs=local.activity_logs,
        connection_lifecycle=ProfileConnectionLifecycleService(profiles, store, ec2, rds_tunnels),
    )
