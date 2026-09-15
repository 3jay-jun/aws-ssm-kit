"""Presentation-neutral MFA recovery for any authenticated application action."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock

from aws_connect.application.authentication_service import OperationCoordinator
from aws_connect.application.operations import (
    MAX_PENDING_AUTHENTICATION_OPERATIONS,
    OperationContext,
    OperationResult,
    OperationState,
    authentication_capacity_error,
    execute_operation,
    new_operation_id,
)
from aws_connect.application.ports import Clock
from aws_connect.domain.errors import CredentialValidationError, MfaValidationError


@dataclass(slots=True)
class _PendingAction[T]:
    expires_at: datetime
    action: Callable[[], T] = field(repr=False)


class AuthenticatedOperationCoordinator:
    """Resume an action exactly once after a shared MFA refresh challenge."""

    def __init__(self, authentication: OperationCoordinator, clock: Clock) -> None:
        self._authentication = authentication
        self._clock = clock
        self._pending: dict[str, _PendingAction[object]] = {}
        self._lock = Lock()

    def start[T](self, selector: str | int | None, action: Callable[[], T]) -> OperationResult[T]:
        return self.start_long(selector, lambda _context: action())

    def start_long[T](
        self,
        selector: str | int | None,
        action: Callable[[OperationContext], T],
        context: OperationContext | None = None,
    ) -> OperationResult[T]:
        """Authenticate a long operation without nesting its typed result contract."""

        initial = context or OperationContext(operation_id=new_operation_id())
        self._sweep_expired()
        attempted = self._invoke(initial.operation_id, lambda: action(initial))
        if (
            attempted.state is not OperationState.FAILED
            or not isinstance(attempted.error, CredentialValidationError)
            or attempted.error.message_code != "auth.mfa_required"
        ):
            return attempted
        # The action is the authority on whether its cached credentials are usable.
        # A feature-level MFA-required result must not be short-circuited by the
        # expiry-only local status projection.  Discard only the temporary session;
        # profile access credentials remain protected and unchanged.
        refresh = self._authentication.start_refresh(selector, discard_cached_session=True)
        rebound = initial.rebound(refresh.operation_id)
        if refresh.state is not OperationState.MFA_REQUIRED or refresh.challenge is None:
            return OperationResult(refresh.operation_id, refresh.state, error=refresh.error)
        with self._lock:
            at_capacity = len(self._pending) >= MAX_PENDING_AUTHENTICATION_OPERATIONS
            if not at_capacity:
                self._pending[refresh.operation_id] = _PendingAction(
                    refresh.challenge.expires_at, lambda: action(rebound)
                )
        if at_capacity:
            self._authentication.cancel(refresh.operation_id)
            return OperationResult(
                refresh.operation_id,
                OperationState.FAILED,
                error=authentication_capacity_error(),
            )
        return OperationResult(
            refresh.operation_id, OperationState.MFA_REQUIRED, challenge=refresh.challenge
        )

    def resume[T](self, operation_id: str, mfa_code: str | None) -> OperationResult[T]:
        self._sweep_expired(exclude=operation_id)
        with self._lock:
            pending = self._pending.pop(operation_id, None)
        if pending is None:
            return self._failure(operation_id)
        if self._clock.now() >= pending.expires_at:
            self._authentication.cancel(operation_id)
            return OperationResult(
                operation_id,
                OperationState.FAILED,
                error=MfaValidationError("mfa.challenge.expired", "MFA challenge expired"),
            )
        refreshed = self._authentication.resume(operation_id, mfa_code)
        if refreshed.state is not OperationState.SUCCEEDED:
            return OperationResult(
                operation_id,
                refreshed.state,
                error=refreshed.error,
                challenge=refreshed.challenge,
            )
        return self._invoke(operation_id, pending.action)  # type: ignore[arg-type]

    def _sweep_expired(self, *, exclude: str | None = None) -> None:
        # OperationCoordinator owns the clock and authoritatively rejects expired
        # challenges. Its resume clears those entries; here we avoid retaining
        # request callables by opportunistically probing only known expiry values.
        now = self._clock.now()
        with self._lock:
            expired = [
                key
                for key, value in self._pending.items()
                if key != exclude and now >= value.expires_at
            ]
            for key in expired:
                self._pending.pop(key, None)
                self._authentication.cancel(key)

    def cancel(self, operation_id: str) -> None:
        """Drop a challenge when a non-interactive adapter cannot collect MFA."""

        with self._lock:
            self._pending.pop(operation_id, None)
        self._authentication.cancel(operation_id)

    @staticmethod
    def _invoke[T](operation_id: str, action: Callable[[], T]) -> OperationResult[T]:
        return execute_operation(
            lambda _context: action(), OperationContext(operation_id=operation_id)
        )

    @staticmethod
    def _failure[T](operation_id: str) -> OperationResult[T]:
        return OperationResult(
            operation_id,
            OperationState.FAILED,
            error=CredentialValidationError(
                "auth.operation.not_resumable",
                "Authenticated operation was already resumed or expired",
            ),
        )
