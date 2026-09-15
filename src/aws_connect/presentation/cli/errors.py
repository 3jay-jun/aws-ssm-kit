"""Single CLI mapping for every typed application error."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aws_connect.domain.errors import (
    ApplicationError,
    AwsNetworkError,
    AwsPermissionError,
    ConfigurationError,
    CredentialValidationError,
    DataProtectionError,
    MfaValidationError,
    PluginExecutionError,
    PortAlreadyInUseError,
    S3TransferError,
    SecretAccessError,
    TargetNotConnectedError,
)

_EXIT_CODES: dict[type[ApplicationError], int] = {
    ConfigurationError: 10,
    DataProtectionError: 10,
    CredentialValidationError: 20,
    MfaValidationError: 20,
    AwsPermissionError: 30,
    AwsNetworkError: 40,
    S3TransferError: 40,
    TargetNotConnectedError: 50,
    PortAlreadyInUseError: 50,
    SecretAccessError: 50,
    PluginExecutionError: 60,
}


@dataclass(frozen=True, slots=True)
class CliError:
    exit_code: int
    payload: dict[str, Any]


def map_error(error: ApplicationError) -> CliError:
    """Map typed errors without inspecting their text."""

    return CliError(
        _EXIT_CODES.get(type(error), 70),
        {
            "error": {
                "code": error.message_code,
                "correlation_id": error.correlation_id,
                "retryable": error.retryable,
                "aws_service": error.aws_service,
                "aws_action": error.aws_action,
            }
        },
    )


def map_unexpected(error: Exception) -> CliError:
    """Map unknown failures without serializing their potentially sensitive message."""

    return map_error(
        ApplicationError(
            message_code="internal.error",
            technical_cause=type(error).__name__,
        )
    )


def render_human_error(error: ApplicationError) -> str:
    """Render safe diagnostics and the exact IAM action without technical causes."""

    requirement = (
        f"; required IAM action: {error.aws_service}:{error.aws_action}"
        if isinstance(error, AwsPermissionError)
        and error.aws_service is not None
        and error.aws_action is not None
        else ""
    )
    return f"{error.message_code}{requirement} (correlation: {error.correlation_id})"


def mapped_error_types() -> frozenset[type[ApplicationError]]:
    return frozenset(_EXIT_CODES)
