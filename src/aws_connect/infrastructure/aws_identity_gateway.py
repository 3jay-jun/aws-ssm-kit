"""boto3 STS adapter with typed AWS failure translation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from aws_connect.application.ports import AwsIdentity, IssuedSession
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import (
    ApplicationError,
    AwsNetworkError,
    AwsPermissionError,
    CredentialValidationError,
    MfaValidationError,
    TargetNotConnectedError,
)


class Boto3IdentityGateway:
    """Use explicit credentials only; never read the ambient AWS credential chain."""

    def get_identity(self, credentials: PlainCredentials, region: str) -> AwsIdentity:
        client = self._client(credentials, region)
        try:
            response = client.get_caller_identity()
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="sts", action="GetCallerIdentity") from error
        arn = str(response["Arn"])
        return AwsIdentity(
            account_id=str(response["Account"]), user_id=_user_from_arn(arn), arn=arn
        )

    def get_session_token(
        self,
        credentials: PlainCredentials,
        region: str,
        mfa_arn: str,
        mfa_code: str,
    ) -> IssuedSession:
        client = self._client(credentials, region)
        try:
            response = client.get_session_token(
                DurationSeconds=43200, SerialNumber=mfa_arn, TokenCode=mfa_code
            )
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="sts", action="GetSessionToken") from error
        raw: dict[str, Any] = response["Credentials"]
        expiration = raw["Expiration"]
        if not isinstance(expiration, datetime):
            raise CredentialValidationError(
                message_code="credentials.expiration.invalid",
                technical_cause="STS returned a non-datetime Expiration",
                aws_service="sts",
                aws_action="GetSessionToken",
            )
        return IssuedSession(
            PlainCredentials(
                access_key=str(raw["AccessKeyId"]),
                secret_key=str(raw["SecretAccessKey"]),
                session_token=str(raw["SessionToken"]),
            ),
            expiration,
        )

    @staticmethod
    def _client(credentials: PlainCredentials, region: str) -> Any:
        return boto3.client(
            "sts",
            region_name=region,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_session_token=credentials.session_token,
        )


def translate_aws_error(
    error: ClientError | BotoCoreError, *, service: str, action: str
) -> ApplicationError:
    """Translate botocore failures once, using stable AWS error codes."""

    if isinstance(error, ClientError):
        code = str(error.response.get("Error", {}).get("Code", "Unknown"))
        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
            return AwsPermissionError(
                "aws.permission.denied", code, aws_service=service, aws_action=action
            )
        if code in {"MultiFactorAuthenticationFailed", "InvalidAuthenticationCode"}:
            return MfaValidationError(
                "mfa.code.rejected", code, aws_service=service, aws_action=action
            )
        if code in {"InvalidClientTokenId", "SignatureDoesNotMatch", "UnrecognizedClientException"}:
            message = (
                "credentials.access_key.inactive"
                if code == "InvalidClientTokenId"
                else "credentials.invalid"
            )
            return CredentialValidationError(message, code, aws_service=service, aws_action=action)
        if code in {"ExpiredToken", "ExpiredTokenException"}:
            return CredentialValidationError(
                "credentials.session.expired", code, aws_service=service, aws_action=action
            )
        if code in {"TargetNotConnected", "InvalidInstanceId"}:
            return TargetNotConnectedError(
                "ec2.target.not_online",
                code,
                aws_service=service,
                aws_action=action,
            )
    return AwsNetworkError(
        "aws.network.error",
        type(error).__name__,
        retryable=True,
        aws_service=service,
        aws_action=action,
    )


def _user_from_arn(arn: str) -> str:
    resource = arn.partition(":user/")[2]
    if resource:
        return resource.rsplit("/", maxsplit=1)[-1]
    assumed = arn.partition(":assumed-role/")[2]
    if assumed:
        return assumed.rsplit("/", maxsplit=1)[-1]
    return arn.rsplit("/", maxsplit=1)[-1]
