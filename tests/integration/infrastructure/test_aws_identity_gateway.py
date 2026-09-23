from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.stub import Stubber

from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import (
    AwsPermissionError,
    CredentialValidationError,
    MfaValidationError,
)
from aws_connect.infrastructure.aws_identity_gateway import Boto3IdentityGateway


def credentials() -> PlainCredentials:
    return PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value")


def stubbed_gateway(monkeypatch) -> tuple[Boto3IdentityGateway, Stubber]:
    client = boto3.client(
        "sts",
        region_name="ap-northeast-2",
        aws_access_key_id="ACCESSKEYTEST0001",
        aws_secret_access_key="not-sensitive-test-value",  # pragma: allowlist secret
    )
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client)
    return Boto3IdentityGateway(), Stubber(client)


def test_get_identity_uses_stubber(monkeypatch) -> None:
    gateway, stubber = stubbed_gateway(monkeypatch)
    stubber.add_response(
        "get_caller_identity",
        {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/team/developer",
            "UserId": "fixture-user-id",
        },
    )
    with stubber:
        identity = gateway.get_identity(credentials(), "ap-northeast-2")

    assert identity.account_id == "123456789012"
    assert identity.user_id == "developer"


@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        ("AccessDenied", AwsPermissionError),
        ("MultiFactorAuthenticationFailed", MfaValidationError),
        ("InvalidClientTokenId", CredentialValidationError),
        ("SignatureDoesNotMatch", CredentialValidationError),
    ],
)
def test_typed_sts_errors(monkeypatch, code, error_type) -> None:
    gateway, stubber = stubbed_gateway(monkeypatch)
    stubber.add_client_error("get_caller_identity", service_error_code=code)
    with stubber, pytest.raises(error_type):
        gateway.get_identity(credentials(), "ap-northeast-2")


def test_get_session_token_uses_mfa_parameters(monkeypatch) -> None:
    gateway, stubber = stubbed_gateway(monkeypatch)
    expiration = datetime.now(UTC) + timedelta(hours=36)
    expected = {
        "DurationSeconds": 129600,
        "SerialNumber": "arn:aws:iam::123456789012:mfa/developer",
        "TokenCode": "123456",
    }
    stubber.add_response(
        "get_session_token",
        {
            "Credentials": {
                "AccessKeyId": "SESSIONKEYTEST001",
                "SecretAccessKey": "x" * 40,
                "SessionToken": "session-token-fixture-value",
                "Expiration": expiration,
            }
        },
        expected,
    )
    with stubber:
        issued = gateway.get_session_token(
            credentials(),
            "ap-northeast-2",
            expected["SerialNumber"],
            "123456",
            duration_seconds=129600,
        )

    assert issued.expires_at_utc == expiration


def test_get_session_token_omits_mfa_parameters_when_disabled(monkeypatch) -> None:
    gateway, stubber = stubbed_gateway(monkeypatch)
    expiration = datetime.now(UTC) + timedelta(hours=12)
    stubber.add_response(
        "get_session_token",
        {
            "Credentials": {
                "AccessKeyId": "SESSIONKEYTEST001",
                "SecretAccessKey": "x" * 40,
                "SessionToken": "session-token-fixture-value",
                "Expiration": expiration,
            }
        },
        {"DurationSeconds": 43200},
    )

    with stubber:
        issued = gateway.get_session_token(
            credentials(), "ap-northeast-2", None, None, duration_seconds=43200
        )

    assert issued.expires_at_utc == expiration
