import boto3
import pytest
from botocore.stub import Stubber

from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import AwsPermissionError, SecretAccessError
from aws_connect.infrastructure.aws_secrets_gateway import Boto3SecretsGateway


def credentials() -> PlainCredentials:
    return PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value", "test-token")


def client():
    return boto3.client(
        "secretsmanager",
        region_name="ap-northeast-2",
        aws_access_key_id="ACCESSKEYTEST0001",
        aws_secret_access_key="not-sensitive-test-value",  # pragma: allowlist secret
        aws_session_token="test-token",  # pragma: allowlist secret
    )


def test_get_secret_value_uses_only_direct_get_contract(monkeypatch) -> None:
    secrets = client()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: secrets)
    stubber = Stubber(secrets)
    raw = '{"host":"db.internal","password":"private-fixture"}'  # pragma: allowlist secret
    stubber.add_response(
        "get_secret_value",
        {
            "ARN": "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:db-dev",
            "Name": "db-dev",
            "VersionId": "a" * 32,
            "SecretString": raw,
            "VersionStages": ["AWSCURRENT"],
        },
        {"SecretId": "db-dev"},  # pragma: allowlist secret
    )

    with stubber:
        result = Boto3SecretsGateway().get_secret_value(credentials(), "ap-northeast-2", "db-dev")

    assert result.secret_string == raw
    assert result.version_stages == ("AWSCURRENT",)


def test_get_maps_permission_not_found_and_binary_without_leaking_response(monkeypatch) -> None:
    secrets = client()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: secrets)
    gateway = Boto3SecretsGateway()

    denied = Stubber(secrets)
    denied.add_client_error(
        "get_secret_value",
        service_error_code="AccessDeniedException",
        service_message="private-value-must-not-escape",
        expected_params={"SecretId": "db-dev"},  # pragma: allowlist secret
    )
    with denied, pytest.raises(AwsPermissionError) as caught:
        gateway.get_secret_value(credentials(), "ap-northeast-2", "db-dev")
    assert caught.value.aws_action == "GetSecretValue"
    assert "private-value" not in caught.value.technical_cause

    missing = Stubber(secrets)
    missing.add_client_error(
        "get_secret_value",
        service_error_code="ResourceNotFoundException",
        expected_params={"SecretId": "missing"},  # pragma: allowlist secret
    )
    with missing, pytest.raises(SecretAccessError, match="secret.not_found"):
        gateway.get_secret_value(credentials(), "ap-northeast-2", "missing")

    binary = Stubber(secrets)
    binary.add_response(
        "get_secret_value",
        {
            "ARN": "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:binary",
            "Name": "binary",
            "SecretBinary": b"binary-value",
        },
        {"SecretId": "binary"},  # pragma: allowlist secret
    )
    with binary, pytest.raises(SecretAccessError, match="secret.binary.unsupported"):
        gateway.get_secret_value(credentials(), "ap-northeast-2", "binary")


def test_optional_list_paginates_and_has_distinct_permission_boundary(monkeypatch) -> None:
    secrets = client()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: secrets)
    gateway = Boto3SecretsGateway()
    stubber = Stubber(secrets)
    stubber.add_response(
        "list_secrets",
        {
            "SecretList": [
                {
                    "ARN": "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:first",
                    "Name": "first",
                }
            ],
            "NextToken": "next",
        },
        {},
    )
    stubber.add_response(
        "list_secrets",
        {
            "SecretList": [
                {
                    "ARN": "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:second",
                    "Name": "second",
                }
            ]
        },
        {"NextToken": "next"},
    )
    with stubber:
        result = gateway.list_secrets(credentials(), "ap-northeast-2")
    assert [item.name for item in result] == ["first", "second"]

    denied = Stubber(secrets)
    denied.add_client_error("list_secrets", service_error_code="AccessDeniedException")
    with denied, pytest.raises(AwsPermissionError) as caught:
        gateway.list_secrets(credentials(), "ap-northeast-2")
    assert caught.value.aws_action == "ListSecrets"


def test_put_secret_value_sends_only_explicit_secret_string() -> None:
    secrets = client()
    gateway = Boto3SecretsGateway(lambda _credentials, _region: secrets)
    stubber = Stubber(secrets)
    secret_string = '{"port":5432}'  # pragma: allowlist secret
    stubber.add_response(
        "put_secret_value",
        {
            "ARN": "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:db-dev",
            "Name": "db-dev",
            "VersionId": "b" * 32,
            "VersionStages": ["AWSCURRENT"],
        },
        {"SecretId": "db-dev", "SecretString": secret_string},  # pragma: allowlist secret
    )

    with stubber:
        version = gateway.put_secret_value(credentials(), "ap-northeast-2", "db-dev", secret_string)

    assert version == "b" * 32
