"""Explicit-credential boto3 adapters for EC2 Session Manager use cases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from aws_connect.application.ports import (
    Ec2Instance,
    Ec2Metadata,
    ManagedInstance,
    StartedSsmSession,
)
from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.infrastructure.aws_diagnostics import observed_client
from aws_connect.infrastructure.aws_identity_gateway import translate_aws_error


class Boto3ManagedInstanceGateway:
    """List online managed nodes and own interactive SSM sessions."""

    def list_online(self, credentials: PlainCredentials, region: str) -> list[ManagedInstance]:
        return self._list(credentials, region, online_only=True)

    def list_managed(self, credentials: PlainCredentials, region: str) -> list[ManagedInstance]:
        """Return every EC2 managed node so inventory can expose SSM Offline state."""

        return self._list(credentials, region, online_only=False)

    def _list(
        self, credentials: PlainCredentials, region: str, *, online_only: bool
    ) -> list[ManagedInstance]:
        client = _client("ssm", credentials, region)
        filters = [{"Key": "ResourceType", "Values": ["EC2Instance"]}]
        if online_only:
            filters.insert(0, {"Key": "PingStatus", "Values": ["Online"]})
        request: dict[str, Any] = {"Filters": filters}
        result: list[ManagedInstance] = []
        try:
            while True:
                response = client.describe_instance_information(**request)
                result.extend(
                    ManagedInstance(
                        instance_id=str(item["InstanceId"]),
                        ping_status=str(item.get("PingStatus", "Unknown")),
                        ip_address=_optional_string(item.get("IPAddress")),
                        platform_name=_optional_string(item.get("PlatformName")),
                    )
                    for item in response.get("InstanceInformationList", [])
                    if not online_only or item.get("PingStatus") == "Online"
                )
                next_token = response.get("NextToken")
                if not next_token:
                    break
                request["NextToken"] = next_token
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(
                error, service="ssm", action="DescribeInstanceInformation"
            ) from error
        return result

    def start_session(
        self,
        credentials: PlainCredentials,
        region: str,
        target: str,
        document_name: str | None = None,
        parameters: Mapping[str, Sequence[str]] | None = None,
    ) -> StartedSsmSession:
        client = _client("ssm", credentials, region)
        request: dict[str, Any] = {"Target": target}
        if document_name is not None:
            request["DocumentName"] = document_name
        if parameters is not None:
            # Keep this as an SDK-native mapping. JSON serialization belongs only
            # to the Session Manager Plugin process boundary.
            request["Parameters"] = {key: list(values) for key, values in parameters.items()}
        try:
            response = client.start_session(**request)
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="ssm", action="StartSession") from error
        return StartedSsmSession(
            session_id=str(response["SessionId"]),
            stream_url=str(response["StreamUrl"]),
            token_value=str(response["TokenValue"]),
        )

    def end_session(self, credentials: PlainCredentials, region: str, session_id: str) -> None:
        client = _client("ssm", credentials, region)
        try:
            client.terminate_session(SessionId=session_id)
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="ssm", action="TerminateSession") from error


class Boto3Ec2MetadataGateway:
    """EC2 inventory, metadata enrichment, and guarded power actions."""

    def list_inventory(self, credentials: PlainCredentials, region: str) -> list[Ec2Instance]:
        client = _client("ec2", credentials, region)
        request: dict[str, Any] = {}
        result: list[Ec2Instance] = []
        try:
            while True:
                response = client.describe_instances(**request)
                for reservation in response.get("Reservations", []):
                    for item in reservation.get("Instances", []):
                        state = str(item.get("State", {}).get("Name", "unknown"))
                        if state not in {"running", "stopped"}:
                            continue
                        result.append(
                            Ec2Instance(
                                instance_id=str(item["InstanceId"]),
                                state=state,
                                name=_name_tag(item.get("Tags", [])),
                                tags=_instance_tags(item.get("Tags", [])),
                                private_ip_address=_optional_string(item.get("PrivateIpAddress")),
                                platform_name=_optional_string(
                                    item.get("PlatformDetails") or item.get("Platform")
                                ),
                            )
                        )
                next_token = response.get("NextToken")
                if not next_token:
                    break
                request["NextToken"] = next_token
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="ec2", action="DescribeInstances") from error
        return result

    def describe(
        self, credentials: PlainCredentials, region: str, instance_ids: Sequence[str]
    ) -> dict[str, Ec2Metadata]:
        if not instance_ids:
            return {}
        client = _client("ec2", credentials, region)
        result: dict[str, Ec2Metadata] = {}
        try:
            for offset in range(0, len(instance_ids), 100):
                response = client.describe_instances(
                    InstanceIds=list(instance_ids[offset : offset + 100])
                )
                for reservation in response.get("Reservations", []):
                    for item in reservation.get("Instances", []):
                        instance_id = str(item["InstanceId"])
                        result[instance_id] = Ec2Metadata(
                            instance_id=instance_id,
                            name=_name_tag(item.get("Tags", [])),
                            tags=_instance_tags(item.get("Tags", [])),
                            private_ip_address=_optional_string(item.get("PrivateIpAddress")),
                        )
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="ec2", action="DescribeInstances") from error
        return result

    def start_instance(self, credentials: PlainCredentials, region: str, instance_id: str) -> None:
        client = _client("ec2", credentials, region)
        self._run_power_action(client, "start_instances", "StartInstances", instance_id)

    def reboot_instance(self, credentials: PlainCredentials, region: str, instance_id: str) -> None:
        client = _client("ec2", credentials, region)
        self._run_power_action(client, "reboot_instances", "RebootInstances", instance_id)

    @staticmethod
    def _run_power_action(client: Any, method_name: str, action: str, instance_id: str) -> None:
        method = getattr(client, method_name)
        try:
            method(InstanceIds=[instance_id], DryRun=True)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code != "DryRunOperation":
                raise translate_aws_error(error, service="ec2", action=action) from error
        except BotoCoreError as error:
            raise translate_aws_error(error, service="ec2", action=action) from error
        try:
            method(InstanceIds=[instance_id])
        except (ClientError, BotoCoreError) as error:
            raise translate_aws_error(error, service="ec2", action=action) from error


def _client(service: str, credentials: PlainCredentials, region: str) -> Any:
    return observed_client(
        boto3.client(
            service,
            region_name=region,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_session_token=credentials.session_token,
        )
    )


def _optional_string(value: object) -> str | None:
    return str(value) if value else None


def _name_tag(tags: list[dict[str, Any]]) -> str | None:
    return next(
        (str(tag["Value"]) for tag in tags if tag.get("Key") == "Name" and tag.get("Value")),
        None,
    )


def _instance_tags(tags: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    return tuple((str(tag["Key"]), str(tag.get("Value", ""))) for tag in tags if "Key" in tag)
