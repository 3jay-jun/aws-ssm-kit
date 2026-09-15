"""GUI rendering of the shared authenticated-operation state machine."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QWidget

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.operations import (
    OperationContext,
    OperationResult,
    OperationState,
    ProgressEvent,
)
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.presentation.gui.tasks import GuiTaskRunner, TaskHandle

MfaCodeProvider = Callable[[QWidget, str], str | None]


class AuthenticatedGuiRunner:
    """Collect MFA in the GUI thread and resume the exact Application action once."""

    def __init__(
        self,
        coordinator: AuthenticatedOperationCoordinator,
        tasks: GuiTaskRunner,
        parent: QWidget,
        mfa_code_provider: MfaCodeProvider,
    ) -> None:
        self._coordinator = coordinator
        self._tasks = tasks
        self._parent = parent
        self._mfa_code_provider = mfa_code_provider

    def submit(
        self,
        selector: str | int | None,
        action: Callable[[], object],
        on_success: Callable[[Any], None],
        on_error: Callable[[ApplicationError], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._tasks.submit(
            lambda: self._coordinator.start(selector, action),
            lambda result: self._handle(result, on_success, on_error, on_cancel),
            on_error,
        )

    def _handle(
        self,
        result: OperationResult[object],
        on_success: Callable[[Any], None],
        on_error: Callable[[ApplicationError], None],
        on_cancel: Callable[[], None] | None,
    ) -> None:
        if result.state is OperationState.MFA_REQUIRED and result.challenge is not None:
            code = self._mfa_code_provider(self._parent, result.challenge.device_arn)
            self._tasks.submit(
                lambda: self._coordinator.resume(result.operation_id, code),
                lambda resumed: self._handle(resumed, on_success, on_error, on_cancel),
                on_error,
            )
            return
        if result.state is OperationState.SUCCEEDED:
            on_success(result.value)
        elif result.state is OperationState.CANCELLED:
            if on_cancel is not None:
                on_cancel()
        elif result.error is not None:
            on_error(result.error)
        else:
            on_error(
                ConfigurationError(
                    "auth.operation.invalid_state",
                    "Authenticated operation completed without a value or typed error",
                )
            )

    def submit_long(
        self,
        selector: str | int | None,
        action: Callable[[OperationContext], object],
        on_success: Callable[[Any], None],
        on_error: Callable[[ApplicationError], None],
        on_progress: Callable[[ProgressEvent], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> TaskHandle:
        """Run a session operation with one context across auth, progress and cancel."""

        progress_handler = on_progress or (lambda _event: None)
        return self._tasks.submit_cancellable(
            lambda token, report: self._coordinator.start_long(
                selector,
                action,
                OperationContext(cancellation=token, progress=report),
            ),
            lambda result: self._handle(result, on_success, on_error, on_cancel),
            on_error,
            progress_handler,
        )
