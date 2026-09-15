from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from aws_connect.application.operations import (
    CancellationToken,
    OperationCancelled,
    OperationContext,
)
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import AwsPermissionError, SecretAccessError
from aws_connect.infrastructure.aws_remote_secret_gateway import (
    Boto3RemoteSecretCommandGateway,
)


def credentials() -> PlainCredentials:
    return PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value", "test-token")


def aws_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "redacted"}}, operation)


@pytest.mark.parametrize(
    ("platform", "document"),
    [
        ("Amazon Linux", "AWS-RunShellScript"),
        ("Microsoft Windows Server", "AWS-RunPowerShellScript"),
    ],
)
def test_fixed_template_platform_document_and_eventual_consistency(
    platform: str, document: str
) -> None:
    client = Mock()
    client.send_command.return_value = {"Command": {"CommandId": "command-test"}}
    client.get_command_invocation.side_effect = [
        aws_error("InvocationDoesNotExist", "GetCommandInvocation"),
        {"Status": "InProgress"},
        {
            "Status": "Success",
            "StandardOutputContent": '{"password":"private"}\n',  # pragma: allowlist secret
        },
    ]
    sleeps: list[float] = []
    gateway = Boto3RemoteSecretCommandGateway(
        lambda _credentials, _region: client, sleep=sleeps.append
    )

    result = gateway.get_secret_value(
        credentials(), "ap-northeast-2", "i-online", platform, "db/dev", lambda: None
    )

    client.send_command.assert_called_once_with(
        InstanceIds=["i-online"],
        DocumentName=document,
        Parameters={
            "commands": [
                "aws secretsmanager get-secret-value --secret-id 'db/dev' "
                "--query SecretString --output text"
            ]
        },
        CloudWatchOutputConfig={"CloudWatchOutputEnabled": False},
    )
    assert result.secret_string == '{"password":"private"}'  # pragma: allowlist secret
    assert "private" not in repr(result)
    assert sleeps == [0.5, 0.5]


def test_caller_and_instance_role_permission_failures_are_distinct() -> None:
    caller_client = Mock()
    caller_client.send_command.side_effect = aws_error("AccessDeniedException", "SendCommand")
    gateway = Boto3RemoteSecretCommandGateway(lambda _credentials, _region: caller_client)
    with pytest.raises(AwsPermissionError) as caller:
        gateway.get_secret_value(
            credentials(), "ap-northeast-2", "i-online", "Linux", "db/dev", lambda: None
        )
    assert caller.value.aws_action == "SendCommand"

    role_client = Mock()
    role_client.send_command.return_value = {"Command": {"CommandId": "command-test"}}
    role_client.get_command_invocation.return_value = {
        "Status": "Failed",
        "StatusDetails": "Failed",
        "StandardErrorContent": "AccessDeniedException: not authorized",
    }
    gateway = Boto3RemoteSecretCommandGateway(lambda _credentials, _region: role_client)
    with pytest.raises(SecretAccessError) as role:
        gateway.get_secret_value(
            credentials(), "ap-northeast-2", "i-online", "Linux", "db/dev", lambda: None
        )
    assert role.value.message_code == "secret.relay.instance_role.permission_denied"
    assert role.value.aws_action == "GetSecretValue"


def test_timeout_and_application_cancellation_are_bounded() -> None:
    timeout_client = Mock()
    timeout_client.send_command.return_value = {"Command": {"CommandId": "command-test"}}
    timeout_client.get_command_invocation.return_value = {"Status": "Pending"}
    gateway = Boto3RemoteSecretCommandGateway(
        lambda _credentials, _region: timeout_client,
        sleep=lambda _seconds: None,
        max_attempts=2,
    )
    with pytest.raises(SecretAccessError, match="secret.relay.timeout") as timeout:
        gateway.get_secret_value(
            credentials(), "ap-northeast-2", "i-online", "Linux", "db/dev", lambda: None
        )
    assert timeout.value.retryable
    assert timeout_client.get_command_invocation.call_count == 2

    cancel_client = Mock()
    cancel_client.send_command.return_value = {"Command": {"CommandId": "command-test"}}
    cancel_client.get_command_invocation.return_value = {"Status": "Pending"}
    cancellation = CancellationToken()
    context = OperationContext(cancellation=cancellation)

    def cancel(_seconds: float) -> None:
        cancellation.cancel()

    gateway = Boto3RemoteSecretCommandGateway(
        lambda _credentials, _region: cancel_client, sleep=cancel
    )
    with pytest.raises(OperationCancelled):
        gateway.get_secret_value(
            credentials(),
            "ap-northeast-2",
            "i-online",
            "Linux",
            "db/dev",
            context.raise_if_cancelled,
        )
    assert cancel_client.get_command_invocation.call_count == 1
