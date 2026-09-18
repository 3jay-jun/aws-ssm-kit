"""Use-case boundary logging with explicit completion and typed failure semantics."""

import json
from collections.abc import Callable
from functools import wraps
from inspect import signature

from aws_connect.application.activity_log_service import ActivityLogService
from aws_connect.application.execution_context import current_execution, execution_scope
from aws_connect.application.operations import OperationCancelled, OperationContext
from aws_connect.domain.errors import (
    ApplicationError,
    AwsNetworkError,
    AwsPermissionError,
    CredentialValidationError,
    MfaValidationError,
    PluginExecutionError,
    PortAlreadyInUseError,
    TargetNotConnectedError,
)
from aws_connect.domain.execution_log import ErrorCategory, ExecutionPhase


def logged_operation[**P, T](
    feature: str, action: str, *, completion: bool = True, target_parameter: str | None = None
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorate a use case, never inspect or serialize arbitrary arguments/results."""

    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        parameters = signature(function)

        @wraps(function)
        def invoke(*args: P.args, **kwargs: P.kwargs) -> T:
            recorder: ActivityLogService | None = getattr(args[0], "_activity_logs", None)
            if recorder is None:
                return function(*args, **kwargs)
            bound = parameters.bind(*args, **kwargs)
            target = "-"
            if target_parameter is not None:
                selected = bound.arguments.get(target_parameter)
                if isinstance(selected, str):
                    target = selected
            context = bound.arguments.get("context")
            if not isinstance(context, OperationContext):
                context = next((arg for arg in args if isinstance(arg, OperationContext)), None)
            with execution_scope(
                context.correlation_id if context else None,
                context.operation_id if context else None,
            ) as identity:
                if context is None and "context" in parameters.parameters:
                    bound.arguments["context"] = OperationContext(
                        operation_id=identity.operation_id, correlation_id=identity.correlation_id
                    )
                recorder.record(
                    feature=feature,
                    operation=action,
                    result="SUCCESS",
                    level="INFO",
                    phase=ExecutionPhase.STARTED,
                    message_code=f"{feature}.{action}.started",
                    target=target,
                )
                if context is not None and context.attempt > 1:
                    recorder.record(
                        feature=feature,
                        operation="retry",
                        target=target,
                        result="WARNING",
                        level="INFO",
                        phase=ExecutionPhase.PROGRESS,
                        message_code=f"{feature}.{action}.retry",
                        metadata_json=json.dumps({"attempt": context.attempt}),
                    )
                try:
                    value = function(*bound.args, **bound.kwargs)
                except OperationCancelled:
                    recorder.record(
                        feature=feature,
                        operation=action,
                        result="CANCELLED",
                        level="WARNING",
                        message_code=f"{feature}.{action}.cancelled",
                        target=target,
                    )
                    raise
                except ApplicationError as error:
                    if error.message_code == "auth.mfa_required":
                        recorder.record(
                            feature=feature,
                            operation=action,
                            result="WARNING",
                            level="INFO",
                            phase=ExecutionPhase.PROGRESS,
                            message_code="auth.mfa_required",
                        )
                        raise
                    if not error.execution_logged:
                        record_failure(recorder, feature, action, error, target=target)
                    raise
                except Exception as error:
                    recorder.record(
                        feature=feature,
                        operation=action,
                        result="FAILURE",
                        level="ERROR",
                        message_code=f"{feature}.{action}.failed",
                        error_category=ErrorCategory.INTERNAL,
                        error_code=type(error).__name__,
                        target=target,
                    )
                    raise
                if completion:
                    recorder.record(
                        feature=feature,
                        operation=action,
                        result="SUCCESS",
                        level="INFO",
                        message_code=f"{feature}.{action}.completed",
                        target=target,
                    )
                return value

        return invoke

    return decorate


def record_failure(
    recorder: ActivityLogService,
    feature: str,
    action: str,
    error: ApplicationError,
    *,
    target: str = "-",
) -> None:
    identity = current_execution.get()
    if identity is not None:
        error.correlation_id = identity.correlation_id
    category = error.error_category
    if category is None:
        category = next(
            (
                kind
                for cls, kind in (
                    (AwsPermissionError, ErrorCategory.PERMISSION),
                    (AwsNetworkError, ErrorCategory.NETWORK),
                    (CredentialValidationError, ErrorCategory.AUTHENTICATION),
                    (MfaValidationError, ErrorCategory.AUTHENTICATION),
                    (PluginExecutionError, ErrorCategory.PROCESS),
                    (PortAlreadyInUseError, ErrorCategory.CONFLICT),
                    (TargetNotConnectedError, ErrorCategory.RESOURCE),
                )
                if isinstance(error, cls)
            ),
            ErrorCategory.CONFIGURATION,
        )
    recorder.record(
        feature=feature,
        operation=action,
        result="FAILURE",
        level="ERROR",
        message_code=error.message_code,
        aws_service=error.aws_service,
        aws_action=error.aws_action,
        aws_request_id=error.aws_request_id,
        error_category=category,
        error_code=error.error_code or error.message_code,
        required_permission=error.required_permission,
        retryable=error.retryable,
        target=target,
    )
    error.execution_logged = True
