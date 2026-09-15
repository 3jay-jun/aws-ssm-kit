"""Presentation-neutral activity log contracts and use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aws_connect.application.ports import ActivityEventWriter, Clock, ManagedLogReader
from aws_connect.application.settings_service import SettingsService
from aws_connect.domain.errors import ConfigurationError


@dataclass(frozen=True, slots=True, repr=False)
class ActivityEvent:
    occurred_at: datetime
    feature: str
    target: str
    result: str
    message_code: str
    correlation_id: str | None = None
    operation_id: str | None = None
    aws_service: str | None = None
    aws_action: str | None = None
    retryable: bool | None = None
    masked_detail: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedLogEntry:
    occurred_at: datetime
    feature: str
    target: str
    result: str
    message_code: str
    correlation_id: str | None = None
    operation_id: str | None = None
    aws_service: str | None = None
    aws_action: str | None = None
    retryable: bool | None = None
    masked_detail: str | None = None


class ActivityLogService:
    """Record allowlisted metadata and query masked managed logs."""

    def __init__(
        self,
        settings: SettingsService,
        reader: ManagedLogReader,
        writer: ActivityEventWriter,
        clock: Clock,
    ) -> None:
        self._settings = settings
        self._reader = reader
        self._writer = writer
        self._clock = clock

    def record(
        self,
        *,
        feature: str,
        target: str,
        result: str,
        message_code: str,
        correlation_id: str | None = None,
        operation_id: str | None = None,
        aws_service: str | None = None,
        aws_action: str | None = None,
        retryable: bool | None = None,
        masked_detail: str | None = None,
    ) -> None:
        """Write only allowlisted diagnostics; arbitrary payloads are not accepted."""

        self._writer.write(
            ActivityEvent(
                occurred_at=self._clock.now(),
                feature=feature,
                target=target,
                result=result,
                message_code=message_code,
                correlation_id=correlation_id,
                operation_id=operation_id,
                aws_service=aws_service,
                aws_action=aws_action,
                retryable=retryable,
                masked_detail=masked_detail,
            )
        )

    def recent(self, limit: int = 200) -> list[ManagedLogEntry]:
        if limit < 1 or limit > 500:
            raise ConfigurationError(
                message_code="logs.limit.invalid",
                technical_cause="Managed log limit must be between 1 and 500",
            )
        return self._reader.read_recent(self._settings.get().log_directory, limit)
