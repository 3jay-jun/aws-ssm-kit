"""Application services for local settings and diagnostic log export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aws_connect.application.activity_log_service import ActivityLogService
from aws_connect.application.execution_logging import logged_operation
from aws_connect.application.ports import DiagnosticLogExporter, LoggingConfigurator, SettingsStore
from aws_connect.domain.app_settings import AppSettings, LogLevel
from aws_connect.domain.errors import ConfigurationError

LOG_DIRECTORY_KEY = "log.directory"
LOG_LEVEL_KEY = "log.level"


@dataclass(frozen=True, slots=True)
class UpdateSettingsRequest:
    log_directory: Path | str | None = None
    log_level: str | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticExport:
    path: Path
    files_exported: int


class SettingsService:
    """Expose the two supported settings without leaking key/value persistence."""

    def __init__(
        self,
        store: SettingsStore,
        default_log_directory: Path,
        configurator: LoggingConfigurator | None = None,
    ) -> None:
        self._store = store
        self._default_log_directory = default_log_directory
        self._configurator = configurator
        self._activity_logs: ActivityLogService | None = None

    def bind_execution_log(self, recorder: ActivityLogService) -> None:
        """Resolve the settings/history composition cycle before user operations."""
        self._activity_logs = recorder

    def get(self) -> AppSettings:
        directory = self._store.get_setting(LOG_DIRECTORY_KEY)
        level = self._store.get_setting(LOG_LEVEL_KEY)
        return AppSettings(
            Path(directory) if directory else self._default_log_directory,
            LogLevel.parse(level) if level else LogLevel.INFO,
        )

    @logged_operation("program", "settings_update")
    def update(self, request: UpdateSettingsRequest) -> AppSettings:
        current = self.get()
        directory = (
            request.log_directory if request.log_directory is not None else current.log_directory
        )
        if not str(directory).strip():
            raise ConfigurationError(
                message_code="settings.log_directory.required",
                technical_cause="Log directory must not be empty",
            )
        level = (
            LogLevel.parse(request.log_level)
            if request.log_level is not None
            else current.log_level
        )
        candidate = AppSettings(Path(directory), level)
        self._apply(candidate)
        try:
            self._store.replace_settings(
                {
                    LOG_DIRECTORY_KEY: str(candidate.log_directory),
                    LOG_LEVEL_KEY: candidate.log_level.value,
                }
            )
        except Exception as error:
            self._restore_logging(current, error)
            raise ConfigurationError(
                message_code="settings.persistence.failed",
                technical_cause=type(error).__name__,
            ) from error
        return candidate

    @logged_operation("program", "settings_reset")
    def reset(self) -> AppSettings:
        candidate = AppSettings(self._default_log_directory, LogLevel.INFO)
        current = self.get()
        self._apply(candidate)
        try:
            self._store.replace_settings({LOG_DIRECTORY_KEY: None, LOG_LEVEL_KEY: None})
        except Exception as error:
            self._restore_logging(current, error)
            raise ConfigurationError(
                message_code="settings.persistence.failed",
                technical_cause=type(error).__name__,
            ) from error
        return candidate

    def apply_current(self) -> AppSettings:
        settings = self.get()
        self._apply(settings)
        return settings

    def _apply(self, settings: AppSettings) -> None:
        if self._configurator is not None:
            self._configurator.apply(settings)

    def _restore_logging(self, settings: AppSettings, original: Exception) -> None:
        try:
            self._apply(settings)
        except Exception as rollback_error:
            raise ConfigurationError(
                message_code="settings.rollback.failed",
                technical_cause=(f"{type(original).__name__}/{type(rollback_error).__name__}"),
            ) from rollback_error


class DiagnosticLogService:
    def __init__(self, settings: SettingsService, exporter: DiagnosticLogExporter) -> None:
        self._settings = settings
        self._exporter = exporter

    def export(self, destination: Path) -> DiagnosticExport:
        files_exported = self._exporter.export(
            self._settings.get().log_directory,
            destination,
        )
        return DiagnosticExport(destination.resolve(), files_exported)
