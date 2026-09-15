"""Validated local application settings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from aws_connect.domain.errors import ConfigurationError


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    @classmethod
    def parse(cls, value: str) -> LogLevel:
        try:
            return cls(value.strip().upper())
        except ValueError as error:
            raise ConfigurationError(
                message_code="settings.log_level.invalid",
                technical_cause=f"Unsupported log level: {value!r}",
            ) from error


@dataclass(frozen=True, slots=True)
class AppSettings:
    log_directory: Path
    log_level: LogLevel = LogLevel.INFO

    def __post_init__(self) -> None:
        if not str(self.log_directory).strip():
            raise ConfigurationError(
                message_code="settings.log_directory.required",
                technical_cause="Log directory must not be empty",
            )
