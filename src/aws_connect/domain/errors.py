"""Typed failures shared by every AWS Connect use case."""

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(eq=False)
class ApplicationError(Exception):
    """Presentation-neutral failure with stable diagnostic metadata."""

    message_code: str
    technical_cause: str
    retryable: bool = False
    aws_service: str | None = None
    aws_action: str | None = None
    correlation_id: str = field(default_factory=lambda: str(uuid4()))

    def __str__(self) -> str:
        return self.message_code


class ConfigurationError(ApplicationError):
    """A profile or local setting is missing or invalid."""


class CredentialValidationError(ApplicationError):
    """AWS credentials or their expected identity are invalid."""


class MfaValidationError(ApplicationError):
    """An MFA challenge cannot be completed."""


class AwsPermissionError(ApplicationError):
    """AWS rejected an operation due to missing permission."""


class AwsNetworkError(ApplicationError):
    """AWS could not be reached or returned a transient service failure."""


class TargetNotConnectedError(ApplicationError):
    """The selected AWS target is not connected."""


class PortAlreadyInUseError(ApplicationError):
    """A requested local port is unavailable."""


class PluginExecutionError(ApplicationError):
    """The Session Manager Plugin could not run successfully."""


class SecretAccessError(ApplicationError):
    """A requested secret could not be accessed."""


class S3TransferError(ApplicationError):
    """An S3 transfer failed."""


class DataProtectionError(ApplicationError):
    """Sensitive data could not be protected or recovered."""


APPLICATION_ERROR_TYPES: tuple[type[ApplicationError], ...] = (
    ConfigurationError,
    CredentialValidationError,
    MfaValidationError,
    AwsPermissionError,
    AwsNetworkError,
    TargetNotConnectedError,
    PortAlreadyInUseError,
    PluginExecutionError,
    SecretAccessError,
    S3TransferError,
    DataProtectionError,
)
