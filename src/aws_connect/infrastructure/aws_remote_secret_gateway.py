"""Bounded SSM Run Command adapter for explicit EC2-mediated Secret lookup."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from aws_connect.application.ports import RetrievedSecret
from aws_connect.application.secrets_service import build_remote_secret_command
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import SecretAccessError
from aws_connect.domain.execution_log import ErrorCategory
from aws_connect.infrastructure.aws_diagnostics import observed_client
from aws_connect.infrastructure.aws_identity_gateway import translate_aws_error

ClientFactory = Callable[[PlainCredentials, str], Any]
Sleep = Callable[[float], None]

_TERMINAL_FAILURES = {"Failed", "TimedOut", "Cancelled", "DeliveryTimedOut"}
_PENDING_STATES = {"Pending", "InProgress", "Delayed"}
_MAX_OUTPUT_CHARACTERS = 24_000


class Boto3RemoteSecretCommandGateway:
    """Run only the fixed AWS CLI SecretString command and poll to one terminal state."""

    def __init__(
        self,
        client_factory: ClientFactory | None = None,
        *,
        sleep: Sleep = time.sleep,
        max_attempts: int = 20,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self._client_factory = client_factory or _client
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._poll_interval_seconds = poll_interval_seconds

    def get_secret_value(
        self,
        credentials: PlainCredentials,
        region: str,
        instance_id: str,
        platform_name: str,
        secret_id: str,
        raise_if_cancelled: Callable[[], None],
    ) -> RetrievedSecret:
        client = self._client_factory(credentials, region)
        document = (
            "AWS-RunPowerShellScript"
            if "windows" in platform_name.casefold()
            else "AWS-RunShellScript"
        )
        command = build_remote_secret_command(secret_id)
        raise_if_cancelled()
        try:
            response = client.send_command(
                InstanceIds=[instance_id],
                DocumentName=document,
                Parameters={"commands": [command]},
                CloudWatchOutputConfig={"CloudWatchOutputEnabled": False},
            )
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="ssm", action="SendCommand") from error
        command_id = str(response["Command"]["CommandId"])
        for attempt in range(self._max_attempts):
            raise_if_cancelled()
            invocation = self._get_invocation(client, command_id, instance_id)
            if invocation is None:
                self._wait(attempt)
                continue
            raise_if_cancelled()
            status = str(invocation.get("Status", "Unknown"))
            if status == "Success":
                output = invocation.get("StandardOutputContent")
                if not isinstance(output, str) or len(output) > _MAX_OUTPUT_CHARACTERS:
                    raise SecretAccessError(
                        "secret.relay.output.invalid",
                        "Run Command returned missing or oversized Secret output",
                        aws_service="ssm",
                        aws_action="GetCommandInvocation",
                    )
                normalized_output = (
                    output[:-2]
                    if output.endswith("\r\n")
                    else output[:-1]
                    if output.endswith("\n")
                    else output
                )
                if not normalized_output:
                    raise SecretAccessError(
                        "secret.relay.output.invalid",
                        "Run Command returned empty Secret output",
                        aws_service="ssm",
                        aws_action="GetCommandInvocation",
                    )
                return RetrievedSecret(secret_id, normalized_output)
            if status in _TERMINAL_FAILURES:
                self._raise_terminal_failure(invocation, status)
            if status not in _PENDING_STATES:
                raise SecretAccessError(
                    "secret.relay.command.status_unknown",
                    "Run Command returned an unsupported terminal status",
                    aws_service="ssm",
                    aws_action="GetCommandInvocation",
                )
            self._wait(attempt)
        raise SecretAccessError(
            "secret.relay.timeout",
            "Run Command did not complete within the bounded polling window",
            error_category=ErrorCategory.TIMEOUT,
            error_code="Timeout",
            retryable=True,
            aws_service="ssm",
            aws_action="GetCommandInvocation",
        )

    @staticmethod
    def _get_invocation(client: Any, command_id: str, instance_id: str) -> dict[str, Any] | None:
        try:
            response = client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            return dict(response)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", "Unknown"))
            if code == "InvocationDoesNotExist":
                return None
            raise translate_aws_error(
                error, service="ssm", action="GetCommandInvocation"
            ) from error
        except BotoCoreError as error:
            raise translate_aws_error(
                error, service="ssm", action="GetCommandInvocation"
            ) from error

    def _wait(self, attempt: int) -> None:
        if attempt + 1 < self._max_attempts:
            self._sleep(self._poll_interval_seconds)

    @staticmethod
    def _raise_terminal_failure(invocation: dict[str, Any], status: str) -> None:
        diagnostic = " ".join(
            str(invocation.get(key, "")) for key in ("StandardErrorContent", "StatusDetails")
        ).casefold()
        if re.search(r"\baccessdenied(?:exception)?\b", diagnostic):
            raise SecretAccessError(
                "secret.relay.instance_role.permission_denied",
                "The EC2 instance role cannot call Secrets Manager GetSecretValue",
                error_category=ErrorCategory.PERMISSION,
                error_code="AccessDeniedException",
                required_permission="secretsmanager:GetSecretValue",
                aws_service="secretsmanager",
                aws_action="GetSecretValue",
            )
        raise SecretAccessError(
            "secret.relay.command.failed",
            f"Run Command ended with status {status}",
            error_category=ErrorCategory.TIMEOUT
            if status in {"TimedOut", "DeliveryTimedOut"}
            else ErrorCategory.RESOURCE,
            error_code=status,
            retryable=status in {"TimedOut", "DeliveryTimedOut"},
            aws_service="ssm",
            aws_action="GetCommandInvocation",
        )


def _client(credentials: PlainCredentials, region: str) -> Any:
    return observed_client(
        boto3.client(
            "ssm",
            region_name=region,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_session_token=credentials.session_token,
        )
    )
