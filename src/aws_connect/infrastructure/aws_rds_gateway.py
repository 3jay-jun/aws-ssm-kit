"""Explicit-credential boto3 adapter for the optional RDS endpoint catalog."""

from collections.abc import Callable
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from aws_connect.application.ports import RdsEndpoint
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.infrastructure.aws_identity_gateway import translate_aws_error

ClientFactory = Callable[[PlainCredentials, str], Any]


class Boto3RdsEndpointGateway:
    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self._client_factory = client_factory or _client

    def list_endpoints(self, credentials: PlainCredentials, region: str) -> list[RdsEndpoint]:
        client = self._client_factory(credentials, region)
        result: list[RdsEndpoint] = []
        marker: str | None = None
        try:
            while True:
                parameters = {"Marker": marker} if marker else {}
                response = client.describe_db_instances(**parameters)
                for instance in response.get("DBInstances", []):
                    endpoint = instance.get("Endpoint", {})
                    host = endpoint.get("Address")
                    port = endpoint.get("Port")
                    identifier = instance.get("DBInstanceIdentifier")
                    if (
                        isinstance(host, str)
                        and isinstance(port, int)
                        and isinstance(identifier, str)
                    ):
                        result.append(
                            RdsEndpoint(
                                identifier,
                                host,
                                port,
                                str(instance.get("Engine", "unknown")),
                            )
                        )
                next_marker = response.get("Marker")
                marker = next_marker if isinstance(next_marker, str) else None
                if marker is None:
                    return sorted(result, key=lambda item: item.identifier.casefold())
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="rds", action="DescribeDBInstances") from error


def _client(credentials: PlainCredentials, region: str) -> Any:
    return boto3.client(
        "rds",
        region_name=region,
        aws_access_key_id=credentials.access_key,
        aws_secret_access_key=credentials.secret_key,
        aws_session_token=credentials.session_token,
    )
