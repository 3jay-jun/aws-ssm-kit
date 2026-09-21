from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import Stubber

from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.domain.errors import AwsPermissionError, TargetNotConnectedError
from aws_connect.infrastructure.aws_ssm_gateway import (
    Boto3Ec2MetadataGateway,
    Boto3ManagedInstanceGateway,
)


def credentials() -> PlainCredentials:
    return PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value", "test-token")


def client(service: str):
    return boto3.client(
        service,
        region_name="ap-northeast-2",
        aws_access_key_id="ACCESSKEYTEST0001",
        aws_secret_access_key="not-sensitive-test-value",  # pragma: allowlist secret
        aws_session_token="test-token",  # pragma: allowlist secret
    )


def test_ssm_list_paginates_filters_and_ignores_offline(monkeypatch) -> None:
    ssm = client("ssm")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ssm)
    stubber = Stubber(ssm)
    expected = {
        "Filters": [
            {"Key": "PingStatus", "Values": ["Online"]},
            {"Key": "ResourceType", "Values": ["EC2Instance"]},
        ]
    }
    common = {
        "LastPingDateTime": datetime(2026, 1, 1, tzinfo=UTC),
        "AgentVersion": "3.3.0",
        "IsLatestVersion": True,
        "PlatformType": "Linux",
        "ResourceType": "EC2Instance",
    }
    stubber.add_response(
        "describe_instance_information",
        {
            "InstanceInformationList": [
                {
                    **common,
                    "InstanceId": "i-online",
                    "PingStatus": "Online",
                    "IPAddress": "10.0.0.1",
                    "PlatformName": "Amazon Linux",
                }
            ],
            "NextToken": "next-page",
        },
        expected,
    )
    stubber.add_response(
        "describe_instance_information",
        {
            "InstanceInformationList": [
                {**common, "InstanceId": "i-offline", "PingStatus": "ConnectionLost"}
            ]
        },
        {**expected, "NextToken": "next-page"},
    )

    with stubber:
        instances = Boto3ManagedInstanceGateway().list_online(credentials(), "ap-northeast-2")

    assert [instance.instance_id for instance in instances] == ["i-online"]


def test_ssm_list_managed_includes_offline_without_ping_filter(monkeypatch) -> None:
    ssm = client("ssm")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ssm)
    stubber = Stubber(ssm)
    expected = {"Filters": [{"Key": "ResourceType", "Values": ["EC2Instance"]}]}
    common = {
        "LastPingDateTime": datetime(2026, 1, 1, tzinfo=UTC),
        "AgentVersion": "3.3.0",
        "IsLatestVersion": True,
        "PlatformType": "Linux",
        "ResourceType": "EC2Instance",
    }
    stubber.add_response(
        "describe_instance_information",
        {
            "InstanceInformationList": [
                {**common, "InstanceId": "i-offline", "PingStatus": "ConnectionLost"}
            ]
        },
        expected,
    )

    with stubber:
        instances = Boto3ManagedInstanceGateway().list_managed(credentials(), "ap-northeast-2")

    assert [(item.instance_id, item.ping_status) for item in instances] == [
        ("i-offline", "ConnectionLost")
    ]


def test_start_and_terminate_session_use_stubber(monkeypatch) -> None:
    ssm = client("ssm")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ssm)
    stubber = Stubber(ssm)
    stubber.add_response(
        "start_session",
        {
            "SessionId": "developer-session-test",
            "StreamUrl": "wss://stream.test.invalid",
            "TokenValue": "transport-token-value",
        },
        {"Target": "i-online"},
    )
    stubber.add_response(
        "terminate_session",
        {"SessionId": "developer-session-test"},
        {"SessionId": "developer-session-test"},
    )
    gateway = Boto3ManagedInstanceGateway()

    with stubber:
        session = gateway.start_session(credentials(), "ap-northeast-2", "i-online")
        gateway.end_session(credentials(), "ap-northeast-2", session.session_id)

    assert session.session_id == "developer-session-test"
    assert session.token_value == "transport-token-value"


def test_remote_host_start_uses_native_parameters_mapping(monkeypatch) -> None:
    ssm = client("ssm")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ssm)
    parameters = {
        "host": ["db.example.internal"],
        "portNumber": ["3306"],
        "localPortNumber": ["13306"],
    }
    expected = {
        "Target": "i-online",
        "DocumentName": "AWS-StartPortForwardingSessionToRemoteHost",
        "Parameters": parameters,
    }
    stubber = Stubber(ssm)
    stubber.add_response(
        "start_session",
        {
            "SessionId": "developer-session-test",
            "StreamUrl": "wss://stream.test.invalid",
            "TokenValue": "transport-token-value",
        },
        expected,
    )
    with stubber:
        Boto3ManagedInstanceGateway().start_session(
            credentials(),
            "ap-northeast-2",
            "i-online",
            "AWS-StartPortForwardingSessionToRemoteHost",
            parameters,
        )


def test_start_session_maps_disconnected_target(monkeypatch) -> None:
    ssm = client("ssm")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ssm)
    stubber = Stubber(ssm)
    stubber.add_client_error(
        "start_session",
        service_error_code="TargetNotConnected",
        expected_params={"Target": "i-offline"},
    )
    with stubber, pytest.raises(TargetNotConnectedError) as caught:
        Boto3ManagedInstanceGateway().start_session(credentials(), "ap-northeast-2", "i-offline")
    assert caught.value.aws_action == "StartSession"


def test_ec2_metadata_maps_name_and_permission(monkeypatch) -> None:
    ec2 = client("ec2")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ec2)
    stubber = Stubber(ec2)
    stubber.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-online",
                            "PrivateIpAddress": "10.0.1.1",
                            "Tags": [{"Key": "Name", "Value": "web"}],
                        },
                        {"InstanceId": "i-no-name"},
                    ]
                }
            ]
        },
        {"InstanceIds": ["i-online", "i-no-name"]},
    )
    gateway = Boto3Ec2MetadataGateway()
    with stubber:
        result = gateway.describe(credentials(), "ap-northeast-2", ["i-online", "i-no-name"])
    assert result["i-online"].tags == (("Name", "web"),)
    assert result["i-no-name"].tags == ()
    assert result["i-online"].name == "web"
    assert result["i-no-name"].name is None

    denied = Stubber(ec2)
    denied.add_client_error(
        "describe_instances",
        service_error_code="UnauthorizedOperation",
        expected_params={"InstanceIds": ["i-online"]},
    )
    with denied, pytest.raises(AwsPermissionError) as caught:
        gateway.describe(credentials(), "ap-northeast-2", ["i-online"])
    assert caught.value.aws_service == "ec2"
    assert caught.value.aws_action == "DescribeInstances"


def test_ec2_inventory_paginates_and_keeps_only_running_and_stopped(monkeypatch) -> None:
    ec2 = client("ec2")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ec2)
    stubber = Stubber(ec2)
    stubber.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-running",
                            "State": {"Code": 16, "Name": "running"},
                            "PrivateIpAddress": "10.0.0.1",
                            "PlatformDetails": "Linux/UNIX",
                            "Tags": [{"Key": "Name", "Value": "web"}],
                        }
                    ]
                }
            ],
            "NextToken": "page-2",
        },
        {},
    )
    stubber.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-stopped",
                            "State": {"Code": 80, "Name": "stopped"},
                        },
                        {
                            "InstanceId": "i-terminated",
                            "State": {"Code": 48, "Name": "terminated"},
                        },
                    ]
                }
            ]
        },
        {"NextToken": "page-2"},
    )

    with stubber:
        instances = Boto3Ec2MetadataGateway().list_inventory(credentials(), "ap-northeast-2")

    assert [(item.instance_id, item.state) for item in instances] == [
        ("i-running", "running"),
        ("i-stopped", "stopped"),
    ]


def test_ec2_start_and_reboot_dry_run_before_actual_request(monkeypatch) -> None:
    ec2 = client("ec2")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ec2)
    stubber = Stubber(ec2)
    stubber.add_client_error(
        "start_instances",
        service_error_code="DryRunOperation",
        expected_params={"InstanceIds": ["i-stopped"], "DryRun": True},
    )
    stubber.add_response(
        "start_instances",
        {
            "StartingInstances": [
                {
                    "InstanceId": "i-stopped",
                    "CurrentState": {"Code": 0, "Name": "pending"},
                    "PreviousState": {"Code": 80, "Name": "stopped"},
                }
            ]
        },
        {"InstanceIds": ["i-stopped"]},
    )
    stubber.add_client_error(
        "reboot_instances",
        service_error_code="DryRunOperation",
        expected_params={"InstanceIds": ["i-running"], "DryRun": True},
    )
    stubber.add_response("reboot_instances", {}, {"InstanceIds": ["i-running"]})
    gateway = Boto3Ec2MetadataGateway()

    with stubber:
        gateway.start_instance(credentials(), "ap-northeast-2", "i-stopped")
        gateway.reboot_instance(credentials(), "ap-northeast-2", "i-running")


def test_ec2_inventory_keeps_transitional_rows(monkeypatch):
    ec2 = client("ec2")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: ec2)
    stubber = Stubber(ec2)
    stubber.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {"InstanceId": "i-pending", "State": {"Name": "pending", "Code": 0}},
                        {"InstanceId": "i-stopping", "State": {"Name": "stopping", "Code": 64}},
                    ]
                }
            ]
        },
        {},
    )
    with stubber:
        instances = Boto3Ec2MetadataGateway().list_inventory(credentials(), "ap-northeast-2")
    assert [item.state for item in instances] == ["pending", "stopping"]
