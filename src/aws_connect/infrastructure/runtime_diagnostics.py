"""Local runtime diagnostic probe for the CLI doctor command."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from aws_connect.application.ports import Clock, CredentialProtector, SessionPlugin
from aws_connect.application.settings_service import SettingsService
from aws_connect.application.system_info import (
    DatabaseDiagnostic,
    DataProtectionDiagnostic,
    LogDiagnostic,
    ProfileDiagnostic,
    RuntimeDiagnostics,
    SessionHostDiagnostic,
)
from aws_connect.infrastructure.session_manager_plugin import (
    external_terminal_argv,
    session_host_path,
)
from aws_connect.infrastructure.sqlite_profile_store import SqliteProfileStore


class LocalRuntimeDiagnosticProbe:
    def __init__(
        self,
        store: SqliteProfileStore,
        protector: CredentialProtector,
        plugin: SessionPlugin,
        settings: SettingsService,
        clock: Clock,
    ) -> None:
        self._store = store
        self._protector = protector
        self._plugin = plugin
        self._settings = settings
        self._clock = clock

    def inspect(self) -> RuntimeDiagnostics:
        configured = self._settings.get()
        database = DatabaseDiagnostic(
            path=self._store.path.resolve(),
            exists=self._store.path.is_file(),
            writable=_path_writable(self._store.path),
            migration_version=self._store.current_schema_version(),
            expected_migration_version=self._store.expected_schema_version,
        )
        data_protection = DataProtectionDiagnostic(_dpapi_round_trip(self._protector))
        log_path = configured.log_directory.resolve()
        logging = LogDiagnostic(
            path=log_path,
            level=configured.log_level.value,
            writable=_directory_writable(log_path),
        )
        profiles = self._store.list()
        default = self._store.get_default()
        if default is None:
            profile = ProfileDiagnostic(0, None, "not_configured", None, "not_configured")
        else:
            session = self._store.get_session(default.require_id())
            session_ready = session is not None and not session.needs_refresh(self._clock.now())
            profile = ProfileDiagnostic(
                count=len(profiles),
                default_profile=default.name,
                session_state="ready" if session_ready else "mfa_required",
                region=default.region,
                # A live AWS call would unexpectedly consume credentials/network. The local
                # readiness state is explicit; ``auth validate`` is the reproducible live probe.
                region_access="session_ready" if session_ready else "authentication_required",
            )
        return RuntimeDiagnostics(
            database,
            data_protection,
            logging,
            self._plugin.diagnose(),
            _session_host_diagnostic(),
            profile,
        )


def _dpapi_round_trip(protector: CredentialProtector) -> bool:
    try:
        protected = protector.protect("aws-connect-doctor-probe")
        return protector.unprotect(protected) == "aws-connect-doctor-probe"
    except Exception:
        return False


def _path_writable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.W_OK)


def _directory_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return path.is_dir() and os.access(path, os.W_OK)


def _session_host_diagnostic() -> SessionHostDiagnostic:
    path = session_host_path()
    argv = external_terminal_argv("diagnostic-pipe", "dpapi-protected-diagnostic-key")
    joined = " ".join(argv)
    forbidden = ("SessionId", "StreamUrl", "TokenValue", "plugin_argv")
    return SessionHostDiagnostic(
        path=path,
        mode="sibling_executable" if getattr(sys, "frozen", False) else "module",
        exists=path.is_file(),
        argument_boundary_safe=not any(value in joined for value in forbidden),
    )
