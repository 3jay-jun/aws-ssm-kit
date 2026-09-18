"""Presentation-neutral activity log contracts and use cases."""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from aws_connect.application.execution_context import current_aws_call, current_execution
from aws_connect.application.ports import (
    ActivityEventWriter,
    Clock,
    ExecutionLogRepository,
    ExecutionLogSanitizer,
    ManagedLogReader,
)

if TYPE_CHECKING:
    from aws_connect.application.settings_service import SettingsService
from aws_connect.domain.errors import ConfigurationError
from aws_connect.domain.execution_log import (
    ErrorCategory,
    ExecutionLevel,
    ExecutionLogEvent,
    ExecutionLogFilter,
    ExecutionPhase,
    ExecutionResult,
)


@dataclass(frozen=True, slots=True, repr=False)
class ActivityEvent:
    occurred_at: datetime
    feature: str
    target: str
    result: str
    message_code: str
    operation: str | None = None
    level: str | None = None
    correlation_id: str | None = None
    operation_id: str | None = None
    aws_service: str | None = None
    aws_action: str | None = None
    aws_request_id: str | None = None
    retryable: bool | None = None
    masked_detail: str | None = None
    profile_id: int | None = None
    region: str | None = None
    phase: ExecutionPhase = ExecutionPhase.COMPLETED
    error_category: ErrorCategory | None = None
    error_code: str | None = None
    required_permission: str | None = None
    metadata_json: str = "{}"


@dataclass(frozen=True, slots=True)
class ManagedLogEntry:
    occurred_at: datetime
    feature: str
    target: str
    result: str
    message_code: str
    operation: str | None = None
    level: str | None = None
    correlation_id: str | None = None
    operation_id: str | None = None
    aws_service: str | None = None
    aws_action: str | None = None
    aws_request_id: str | None = None
    retryable: bool | None = None
    masked_detail: str | None = None
    profile_id: int | None = None
    region: str | None = None
    phase: ExecutionPhase = ExecutionPhase.COMPLETED
    error_category: ErrorCategory | None = None
    error_code: str | None = None
    required_permission: str | None = None
    metadata_json: str = "{}"


@dataclass(frozen=True, slots=True)
class ActivityLogQuery:
    period_days: int | None = None
    levels: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    search: str = ""
    errors_only: bool = False


class ActivityLogService:
    """Record allowlisted metadata and query masked managed logs."""

    def __init__(
        self,
        settings: SettingsService,
        reader: ManagedLogReader,
        writer: ActivityEventWriter,
        clock: Clock,
        repository: ExecutionLogRepository | None = None,
        sanitizer: ExecutionLogSanitizer | None = None,
    ) -> None:
        self._settings = settings
        self._reader = reader
        self._writer = writer
        self._clock = clock
        self._repository = repository
        self._sanitizer = sanitizer

    def record(
        self,
        event: ExecutionLogEvent | None = None,
        *,
        feature: str = "program",
        target: str = "-",
        result: str = "SUCCESS",
        message_code: str = "",
        operation: str | None = None,
        level: str | None = None,
        correlation_id: str | None = None,
        operation_id: str | None = None,
        aws_service: str | None = None,
        aws_action: str | None = None,
        aws_request_id: str | None = None,
        retryable: bool | None = None,
        masked_detail: str | None = None,
        profile_id: int | None = None,
        region: str | None = None,
        phase: ExecutionPhase = ExecutionPhase.COMPLETED,
        error_category: ErrorCategory | None = None,
        error_code: str | None = None,
        required_permission: str | None = None,
        metadata_json: str = "{}",
    ) -> None:
        """Write only allowlisted diagnostics; arbitrary payloads are not accepted."""

        identity = current_execution.get()
        if identity is not None:
            correlation_id = correlation_id or identity.correlation_id
            operation_id = operation_id or identity.operation_id
        aws_call = current_aws_call.get()
        if aws_call is not None:
            aws_service = aws_service or aws_call.service
            aws_action = aws_action or aws_call.action
            aws_request_id = aws_request_id or aws_call.request_id
        if event is not None or self._repository is not None:
            if event is None:
                status = ExecutionResult.from_legacy(result)
                event_level = level or (
                    "ERROR"
                    if status is ExecutionResult.FAILURE
                    else "WARNING"
                    if status in {ExecutionResult.WARNING, ExecutionResult.CANCELLED}
                    else "INFO"
                )
                try:
                    metadata = json.loads(metadata_json) if len(metadata_json) <= 8192 else {}
                except (TypeError, ValueError) as error:
                    raise ConfigurationError(
                        "logs.metadata.invalid", "Execution metadata must be a JSON object"
                    ) from error
                if not isinstance(metadata, dict):
                    raise ConfigurationError(
                        "logs.metadata.invalid", "Execution metadata must be a JSON object"
                    )
                metadata.update(
                    {"profile_id": profile_id, "region": region, "retryable": retryable}
                )
                event = ExecutionLogEvent(
                    self._clock.now(),
                    ExecutionLevel(event_level),
                    status,
                    phase,
                    _feature_key(feature),
                    operation or message_code,
                    target,
                    masked_detail or message_code,
                    correlation_id,
                    operation_id,
                    aws_service,
                    aws_action,
                    aws_request_id,
                    error_category,
                    error_code,
                    required_permission,
                    json.dumps(metadata),
                )
            self._record_event(event)
            return
        self._writer.write(
            ActivityEvent(
                occurred_at=self._clock.now(),
                feature=feature,
                operation=operation,
                target=target,
                result=result,
                message_code=message_code,
                level=level,
                correlation_id=correlation_id,
                operation_id=operation_id,
                aws_service=aws_service,
                aws_action=aws_action,
                aws_request_id=aws_request_id,
                retryable=retryable,
                masked_detail=masked_detail,
                profile_id=profile_id,
                region=region,
                phase=phase,
                error_category=error_category,
                error_code=error_code,
                required_permission=required_permission,
                metadata_json=metadata_json,
            )
        )

    def _record_event(self, event: ExecutionLogEvent) -> None:
        if event.occurred_at.tzinfo is None:
            raise ConfigurationError("logs.time.invalid", "Execution timestamps require a timezone")
        if self._sanitizer is None:
            raise ConfigurationError(
                "logs.sanitizer.required", "Execution history requires a masking adapter"
            )
        identity = event.correlation_id or str(uuid4())
        safe = self._sanitizer.sanitize(
            replace(event, correlation_id=identity, operation_id=event.operation_id or str(uuid4()))
        )
        if self._repository is not None:
            self._repository.append_execution_log(safe)
        metadata = json.loads(safe.metadata_json)
        self._writer.write(
            ActivityEvent(
                safe.occurred_at,
                safe.feature,
                safe.target,
                safe.result.value,
                safe.message,
                operation=safe.action,
                level=safe.level.value,
                correlation_id=safe.correlation_id,
                operation_id=safe.operation_id,
                aws_service=safe.aws_service,
                aws_action=safe.aws_action,
                aws_request_id=safe.aws_request_id,
                profile_id=metadata.get("profile_id"),
                region=metadata.get("region"),
                phase=safe.phase,
                error_category=safe.error_category,
                error_code=safe.error_code,
                required_permission=safe.required_permission,
                metadata_json=safe.metadata_json,
            )
        )

    def list(
        self, query: ExecutionLogFilter | None = None, limit: int = 200
    ) -> builtins.list[ExecutionLogEvent]:
        if not 1 <= limit <= 10000:
            raise ConfigurationError(
                "logs.limit.invalid", "Execution log limit must be between 1 and 10000"
            )
        if self._repository is None:
            raise ConfigurationError(
                "logs.repository.required", "Structured execution history is unavailable"
            )
        return self._repository.query_execution_logs(query or ExecutionLogFilter(), limit)

    def list_recent(self, limit: int = 10) -> builtins.list[ExecutionLogEvent]:
        return self.list(ExecutionLogFilter(completed_only=True), limit)

    def load_dashboard_recent(self, limit: int = 5) -> builtins.list[ExecutionLogEvent]:
        entries = self.list_recent(limit)
        record_success(self, "dashboard", "load")
        return entries

    def recent_ec2_connections(self, profile_id: int, region: str) -> dict[str, datetime]:
        """Project successful shell launches from bounded, re-masked retained logs.

        Unscoped legacy events cannot establish a profile/region-specific success.
        """
        entries = (
            [
                execution_entry(event)
                for event in self.list(
                    ExecutionLogFilter(features=("ec2",), completed_only=True), 10000
                )
            ]
            if self._repository is not None
            else self._reader.read_recent(self._settings.get().log_directory, 10000)
        )
        latest: dict[str, datetime] = {}
        for entry in entries:
            if (
                entry.feature == "ec2"
                and entry.message_code == "ec2.connection.succeeded"
                and ExecutionResult.from_legacy(entry.result) is ExecutionResult.SUCCESS
                and entry.profile_id == profile_id
                and entry.region == region
            ):
                previous = latest.get(entry.target)
                if previous is None or entry.occurred_at > previous:
                    latest[entry.target] = entry.occurred_at
        return latest

    def recent(
        self, limit: int = 200, query: ActivityLogQuery | None = None
    ) -> builtins.list[ManagedLogEntry]:
        if limit < 1 or limit > 500:
            raise ConfigurationError(
                message_code="logs.limit.invalid",
                technical_cause="Managed log limit must be between 1 and 500",
            )
        if self._repository is not None:
            selected = query or ActivityLogQuery()
            since = None
            if selected.period_days is not None:
                since = self._clock.now().astimezone().replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) - timedelta(days=selected.period_days - 1)
            return [
                execution_entry(event)
                for event in self.list(
                    ExecutionLogFilter(
                        since=since,
                        levels=selected.levels,
                        features=selected.features,
                        search=selected.search,
                        errors_only=selected.errors_only,
                    ),
                    limit,
                )
            ]
        entries = self._reader.read_recent(self._settings.get().log_directory, limit)
        return filter_activity_logs(entries, query, now=self._clock.now())


def filter_activity_logs(
    entries: list[ManagedLogEntry],
    query: ActivityLogQuery | None,
    *,
    now: datetime,
) -> builtins.list[ManagedLogEntry]:
    if query is None:
        return entries
    threshold = (
        now.astimezone(UTC) - timedelta(days=query.period_days - 1)
        if query.period_days is not None
        else None
    )
    levels = {level.upper() for level in query.levels}
    features = {feature.casefold() for feature in query.features}
    needle = query.search.strip().casefold()
    return [
        entry
        for entry in entries
        if _matches_period(entry, threshold)
        and _matches_level(entry, levels)
        and _matches_feature(entry, features)
        and _matches_errors_only(entry, query.errors_only)
        and _matches_search(entry, needle)
    ]


def _matches_period(entry: ManagedLogEntry, threshold: datetime | None) -> bool:
    return (
        threshold is None or entry.occurred_at.astimezone().date() >= threshold.astimezone().date()
    )


def _matches_level(entry: ManagedLogEntry, levels: set[str]) -> bool:
    return not levels or _entry_level(entry) in levels


def _matches_feature(entry: ManagedLogEntry, features: set[str]) -> bool:
    return not features or _feature_key(entry.feature) in features


def _matches_errors_only(entry: ManagedLogEntry, errors_only: bool) -> bool:
    return not errors_only or _entry_result(entry) == "failure"


def _matches_search(entry: ManagedLogEntry, needle: str) -> bool:
    if not needle:
        return True
    haystack = (
        entry.message_code,
        entry.masked_detail or "",
        entry.operation_id or "",
        entry.correlation_id or "",
        entry.target,
        entry.aws_action or "",
    )
    return any(needle in value.casefold() for value in haystack)


def _entry_level(entry: ManagedLogEntry) -> str:
    if entry.level:
        return entry.level.upper()
    result = _entry_result(entry)
    if result == "failure":
        return "ERROR"
    if result == "warning":
        return "WARNING"
    return "INFO"


def _entry_result(entry: ManagedLogEntry) -> str:
    value = entry.result.casefold()
    if value in {"failed", "failure", "error"}:
        return "failure"
    if value in {"warning", "warn", "notice", "cancelled"}:
        return "warning"
    return "success"


def _feature_key(feature: str) -> str:
    value = feature.casefold()
    if "ec2" in value:
        return "ec2"
    if "rds" in value:
        return "rds"
    if "s3" in value:
        return "s3"
    if "secret" in value:
        return "secrets"
    if "auth" in value or "profile" in value:
        return "auth"
    if "dashboard" in value:
        return "dashboard"
    return "program"


def record_success(
    recorder: ActivityLogService | None,
    feature: str,
    operation: str,
    *,
    target: str = "-",
    profile_id: int | None = None,
    region: str | None = None,
    operation_id: str | None = None,
    warning: bool = False,
    metadata: dict[str, str | int] | None = None,
) -> None:
    """Record a completed use case without accepting payloads or credentials."""
    if recorder is not None:
        recorder.record(
            feature=feature,
            operation=operation,
            target=target,
            result="WARNING" if warning else "SUCCESS",
            level="WARNING" if warning else "INFO",
            message_code=f"{feature}.{operation}.completed",
            profile_id=profile_id,
            region=region,
            operation_id=operation_id,
            metadata_json=json.dumps(metadata or {}),
        )


# Keep the existing public name while exposing the structured-history role.
ExecutionLogService = ActivityLogService


def execution_entry(event: ExecutionLogEvent) -> ManagedLogEntry:
    metadata = json.loads(event.metadata_json)
    return ManagedLogEntry(
        event.occurred_at,
        event.feature,
        event.target,
        event.result.value,
        event.message,
        operation=event.action,
        level=event.level.value,
        correlation_id=event.correlation_id,
        operation_id=event.operation_id,
        aws_service=event.aws_service,
        aws_action=event.aws_action,
        aws_request_id=event.aws_request_id,
        profile_id=metadata.get("profile_id"),
        region=metadata.get("region"),
        phase=event.phase,
        error_category=event.error_category,
        error_code=event.error_code,
        required_permission=event.required_permission,
        metadata_json=event.metadata_json,
    )
