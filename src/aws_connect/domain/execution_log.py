"""Typed, credential-free execution history contracts."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

EXECUTION_LOG_RETENTION_DAYS = 30
EXECUTION_LOG_MAX_ROWS = 10_000


class ExecutionLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ExecutionResult(StrEnum):
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    FAILURE = "FAILURE"
    CANCELLED = "CANCELLED"

    @classmethod
    def from_legacy(cls, value: str) -> "ExecutionResult":
        value = value.upper()
        if value in {"FAILURE", "FAILED", "ERROR"}:
            return cls.FAILURE
        if value == "CANCELLED":
            return cls.CANCELLED
        if value in {"WARNING", "WARN", "NOTICE"}:
            return cls.WARNING
        return cls.SUCCESS


class ExecutionPhase(StrEnum):
    STARTED = "STARTED"
    PROGRESS = "PROGRESS"
    COMPLETED = "COMPLETED"


class ErrorCategory(StrEnum):
    PERMISSION = "PERMISSION"
    AUTHENTICATION = "AUTHENTICATION"
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    RESOURCE = "RESOURCE"
    PROCESS = "PROCESS"
    CONFLICT = "CONFLICT"
    CONFIGURATION = "CONFIGURATION"
    INTERNAL = "INTERNAL"


@dataclass(frozen=True, slots=True, repr=False)
class ExecutionLogEvent:
    occurred_at: datetime
    level: ExecutionLevel
    result: ExecutionResult
    phase: ExecutionPhase
    feature: str
    action: str
    target: str
    message: str
    correlation_id: str | None = None
    operation_id: str | None = None
    aws_service: str | None = None
    aws_action: str | None = None
    aws_request_id: str | None = None
    error_category: ErrorCategory | None = None
    error_code: str | None = None
    required_permission: str | None = None
    metadata_json: str = "{}"


@dataclass(frozen=True, slots=True)
class ExecutionLogFilter:
    since: datetime | None = None
    levels: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    search: str = ""
    errors_only: bool = False
    correlation_id: str | None = None
    operation_id: str | None = None
    completed_only: bool = False
