"""Explicit-credential Secrets Manager adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from aws_connect.application.ports import ListedSecret, RetrievedSecret
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import SecretAccessError
from aws_connect.infrastructure.aws_diagnostics import observed_client
from aws_connect.infrastructure.aws_identity_gateway import translate_aws_error

ClientFactory = Callable[[PlainCredentials, str], Any]


class Boto3SecretsGateway:
    """Call GetSecretValue directly; this adapter never calls ListSecrets."""

    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self._client_factory = client_factory or _client

    def get_secret_value(
        self, credentials: PlainCredentials, region: str, secret_id: str
    ) -> RetrievedSecret:
        client = self._client_factory(credentials, region)
        try:
            response = client.get_secret_value(SecretId=secret_id)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", "Unknown"))
            if code == "ResourceNotFoundException":
                raise SecretAccessError(
                    "secret.not_found",
                    code,
                    aws_service="secretsmanager",
                    aws_action="GetSecretValue",
                ) from error
            if code in {
                "DecryptionFailure",
                "DecryptionFailureException",
                "InvalidParameterException",
                "InvalidRequestException",
            }:
                raise SecretAccessError(
                    "secret.value.unavailable",
                    code,
                    aws_service="secretsmanager",
                    aws_action="GetSecretValue",
                ) from error
            raise translate_aws_error(
                error, service="secretsmanager", action="GetSecretValue"
            ) from error
        except BotoCoreError as error:
            raise translate_aws_error(
                error, service="secretsmanager", action="GetSecretValue"
            ) from error
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise SecretAccessError(
                "secret.binary.unsupported",
                "GetSecretValue returned SecretBinary instead of SecretString",
                aws_service="secretsmanager",
                aws_action="GetSecretValue",
            )
        stages = tuple(str(value) for value in response.get("VersionStages", []))
        version_id = response.get("VersionId")
        return RetrievedSecret(
            secret_id=str(response.get("ARN") or response.get("Name") or secret_id),
            secret_string=secret_string,
            version_id=str(version_id) if version_id else None,
            version_stages=stages,
        )

    def list_secrets(self, credentials: PlainCredentials, region: str) -> list[ListedSecret]:
        """List metadata only when explicitly requested by the optional GUI action."""

        client = self._client_factory(credentials, region)
        result: list[ListedSecret] = []
        token: str | None = None
        try:
            while True:
                parameters = {"NextToken": token} if token else {}
                response = client.list_secrets(**parameters)
                for item in response.get("SecretList", []):
                    name = item.get("Name")
                    arn = item.get("ARN")
                    if isinstance(name, str) and isinstance(arn, str):
                        result.append(ListedSecret(name, arn))
                next_token = response.get("NextToken")
                token = next_token if isinstance(next_token, str) else None
                if token is None:
                    return result
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(
                error, service="secretsmanager", action="ListSecrets"
            ) from error

    def put_secret_value(
        self, credentials: PlainCredentials, region: str, secret_id: str, secret_string: str
    ) -> str | None:
        client = self._client_factory(credentials, region)
        try:
            response = client.put_secret_value(SecretId=secret_id, SecretString=secret_string)
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(
                error, service="secretsmanager", action="PutSecretValue"
            ) from error
        version_id = response.get("VersionId")
        return str(version_id) if version_id else None


def _client(credentials: PlainCredentials, region: str) -> Any:
    return observed_client(
        boto3.client(
            "secretsmanager",
            region_name=region,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_session_token=credentials.session_token,
        )
    )
