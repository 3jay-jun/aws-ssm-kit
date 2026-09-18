"""Shared contracts for resumable and long-running operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from threading import Event
from uuid import uuid4

from aws_connect.application.execution_context import execution_scope
from aws_connect.domain.errors import ApplicationError, ConfigurationError

MAX_PENDING_AUTHENTICATION_OPERATIONS = 128


class OperationState(StrEnum):
    PENDING = "PENDING"
    MFA_REQUIRED = "MFA_REQUIRED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    operation_id: str
    phase: str
    completed: int | None
    total: int | None
    message_code: str
    target: str | None = None


class CancellationToken:
    """Thread-safe cooperative cancellation request."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancellation_requested(self) -> bool:
        return self._event.is_set()


class OperationCancelled(Exception):
    """Internal cooperative-cancellation signal translated to a typed result."""


ProgressReporter = Callable[[ProgressEvent], None]


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Shared identity, cancellation and progress channel for one long operation."""

    operation_id: str = field(default_factory=lambda: str(uuid4()))
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    progress: ProgressReporter | None = field(default=None, repr=False)
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    attempt: int = 1

    def report(
        self,
        phase: str,
        message_code: str,
        *,
        completed: int | None = None,
        total: int | None = None,
        target: str | None = None,
    ) -> None:
        if self.progress is not None:
            self.progress(
                ProgressEvent(
                    self.operation_id,
                    phase,
                    completed,
                    total,
                    message_code,
                    target,
                )
            )

    def raise_if_cancelled(self) -> None:
        if self.cancellation.is_cancellation_requested:
            raise OperationCancelled

    def rebound(self, operation_id: str) -> OperationContext:
        """Keep cancellation/progress ownership while MFA supplies the final ID."""

        return OperationContext(
            operation_id, self.cancellation, self.progress, self.correlation_id, self.attempt
        )


@dataclass(frozen=True, slots=True)
class MfaChallenge:
    operation_id: str
    profile_id: int
    device_arn: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OperationResult[T]:
    operation_id: str
    state: OperationState
    value: T | None = None
    error: ApplicationError | None = None
    challenge: MfaChallenge | None = None


def new_operation_id() -> str:
    return str(uuid4())


def execute_operation[T](
    action: Callable[[OperationContext], T],
    context: OperationContext | None = None,
) -> OperationResult[T]:
    """Translate one Application operation to the shared typed result contract."""

    current = context or OperationContext()
    try:
        current.raise_if_cancelled()
        with execution_scope(current.correlation_id, current.operation_id):
            return OperationResult(current.operation_id, OperationState.SUCCEEDED, action(current))
    except OperationCancelled:
        return OperationResult(current.operation_id, OperationState.CANCELLED)
    except ApplicationError as error:
        return OperationResult(current.operation_id, OperationState.FAILED, error=error)


def authentication_capacity_error() -> ConfigurationError:
    """Return the single typed failure for the shared authentication pending bound."""

    return ConfigurationError(
        "auth.operation.capacity_exceeded",
        "Too many authentication operations are waiting for MFA",
    )
