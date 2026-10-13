"""Application-level system information use case."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from aws_connect import APPLICATION_NAME
from aws_connect.application.ports import PluginDiagnostic


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """Presentation-neutral information returned by the bootstrap slice."""

    application_name: str
    status: str


class SystemInfoService(Protocol):
    """Port shared by the CLI and GUI during bootstrap verification."""

    def get(self) -> SystemInfo:
        """Return current application information."""


@dataclass(frozen=True, slots=True)
class StaticSystemInfoService:
    """Minimal implementation replaced by real use cases in later phases."""

    application_name: str = APPLICATION_NAME

    def get(self) -> SystemInfo:
        """Return a deterministic healthy response."""

        return SystemInfo(application_name=self.application_name, status="ready")


@dataclass(frozen=True, slots=True)
class DatabaseDiagnostic:
    path: Path
    exists: bool
    writable: bool
    migration_version: int
    expected_migration_version: int

    @property
    def ready(self) -> bool:
        return (
            self.exists
            and self.writable
            and self.migration_version == self.expected_migration_version
        )


@dataclass(frozen=True, slots=True)
class DataProtectionDiagnostic:
    available: bool
    provider: str = "Windows DPAPI (current user)"


@dataclass(frozen=True, slots=True)
class LogDiagnostic:
    path: Path
    level: str
    writable: bool


@dataclass(frozen=True, slots=True)
class ProfileDiagnostic:
    count: int
    default_profile: str | None
    session_state: str
    region: str | None
    region_access: str


@dataclass(frozen=True, slots=True)
class SessionHostDiagnostic:
    path: Path
    mode: str
    exists: bool
    argument_boundary_safe: bool


@dataclass(frozen=True, slots=True)
class DriveRuntimeDiagnostic:
    rclone_path: Path
    rclone_available: bool
    winfsp_available: bool
    winfsp_state: str = "unknown"
    winfsp_version: str | None = None

    @property
    def ready(self) -> bool:
        return self.rclone_available and self.winfsp_available


@dataclass(frozen=True, slots=True)
class RuntimeDiagnostics:
    database: DatabaseDiagnostic
    data_protection: DataProtectionDiagnostic
    logging: LogDiagnostic
    session_manager_plugin: PluginDiagnostic
    session_host: SessionHostDiagnostic
    profile: ProfileDiagnostic
    s3_drive: DriveRuntimeDiagnostic | None = None

    @property
    def ready(self) -> bool:
        return self.core_ready and (self.s3_drive is None or self.s3_drive.ready)

    @property
    def core_ready(self) -> bool:
        plugin = self.session_manager_plugin
        return (
            self.database.ready
            and self.data_protection.available
            and self.logging.writable
            and plugin.exists
            and plugin.supported
            and plugin.environment_ready
            and self.session_host.exists
            and self.session_host.argument_boundary_safe
        )

    def to_payload(self) -> dict[str, object]:
        plugin = self.session_manager_plugin
        return {
            "core_ready": self.core_ready,
            "features": {
                "s3_mount": {
                    "ready": self.s3_drive.ready if self.s3_drive else False,
                    "reason": (
                        "rclone_missing"
                        if self.s3_drive and not self.s3_drive.rclone_available
                        else self.s3_drive.winfsp_state
                        if self.s3_drive
                        else "runtime_missing"
                    ),
                }
            },
            "s3_drive": (
                {
                    "rclone_path": str(self.s3_drive.rclone_path),
                    "rclone_available": self.s3_drive.rclone_available,
                    "winfsp_available": self.s3_drive.winfsp_available,
                    "winfsp_state": self.s3_drive.winfsp_state,
                    "winfsp_version": self.s3_drive.winfsp_version,
                    "ready": self.s3_drive.ready,
                    "guidance": "Install WinFsp and provide the packaged rclone.exe.",
                }
                if self.s3_drive
                else None
            ),
            "database": {
                "path": str(self.database.path),
                "exists": self.database.exists,
                "writable": self.database.writable,
                "migration_version": self.database.migration_version,
                "expected_migration_version": self.database.expected_migration_version,
                "ready": self.database.ready,
            },
            "data_protection": {
                "available": self.data_protection.available,
                "provider": self.data_protection.provider,
            },
            "logging": {
                "path": str(self.logging.path),
                "level": self.logging.level,
                "writable": self.logging.writable,
            },
            "session_manager_plugin": {
                "path": str(plugin.path),
                "exists": plugin.exists,
                "version": plugin.version,
                "supported": plugin.supported,
                "environment_ready": plugin.environment_ready,
            },
            "session_host": {
                "path": str(self.session_host.path),
                "mode": self.session_host.mode,
                "exists": self.session_host.exists,
                "argument_boundary_safe": self.session_host.argument_boundary_safe,
            },
            "profile": {
                "count": self.profile.count,
                "default_profile": self.profile.default_profile,
                "session_state": self.profile.session_state,
                "region": self.profile.region,
                "region_access": self.profile.region_access,
            },
        }


class RuntimeDiagnosticProbe(Protocol):
    def inspect(self) -> RuntimeDiagnostics: ...


class DoctorService:
    """Presentation-neutral orchestration for non-destructive local diagnostics."""

    def __init__(self, probe: RuntimeDiagnosticProbe) -> None:
        self._probe = probe

    def get(self) -> tuple[SystemInfo, RuntimeDiagnostics]:
        result = self._probe.inspect()
        return SystemInfo(APPLICATION_NAME, "ready" if result.ready else "degraded"), result
