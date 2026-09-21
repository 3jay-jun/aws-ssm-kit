import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from aws_connect.application.dashboard_service import (
    CapabilityState,
    DashboardService,
    PermissionState,
)
from aws_connect.domain.errors import AwsNetworkError, AwsPermissionError
from aws_connect.domain.execution_log import (
    ExecutionLevel,
    ExecutionLogEvent,
    ExecutionPhase,
    ExecutionResult,
)


def build():
    profiles, sessions, logs, clock, inventory, managed, secrets, s3 = [Mock() for _ in range(8)]
    profiles.resolve.return_value.region = "example-region"
    clock.now.return_value = datetime.now(UTC)
    logs.list.return_value = []
    service = DashboardService(profiles, sessions, logs, clock, inventory, managed, secrets, s3)
    return service, logs, inventory, managed, secrets, s3


def row(result, feature, key):
    return next(
        p for f in result.features if f.feature == feature for p in f.permissions if p.key == key
    )


def test_only_four_read_only_probes_and_unknown_mutations():
    service, _, inventory, managed, secrets, s3 = build()
    result = service.check_permissions(1)
    assert row(result, "ec2", "inventory").state is PermissionState.ALLOWED
    assert row(result, "rds", "forward").state is PermissionState.UNKNOWN
    assert row(result, "secrets", "write").state is PermissionState.UNKNOWN
    assert all(f.state is CapabilityState.PARTIAL for f in result.features)
    assert [call[0] for call in inventory.method_calls] == ["list_inventory"]
    assert [call[0] for call in managed.method_calls] == ["list_managed"]
    assert [call[0] for call in secrets.method_calls] == ["list_secrets"]
    assert [call[0] for call in s3.method_calls] == ["list_buckets"]


@pytest.mark.parametrize(
    "error,expected",
    [
        (AwsPermissionError("aws.permission.denied", "denied"), PermissionState.DENIED),
        (AwsNetworkError("aws.network.failed", "network"), PermissionState.UNKNOWN),
    ],
)
def test_only_typed_permission_errors_are_denied(error, expected):
    service, _, inventory, _, _, _ = build()
    inventory.list_inventory.side_effect = error
    result = service.check_permissions(1)
    assert row(result, "ec2", "inventory").state is expected
    assert row(result, "rds", "inventory").state is expected


def test_history_requires_profile_region_completed_exact_api_and_consistent_resources():
    service, logs, *_ = build()
    event = ExecutionLogEvent(
        datetime.now(UTC),
        ExecutionLevel.INFO,
        ExecutionResult.SUCCESS,
        ExecutionPhase.COMPLETED,
        "s3",
        "upload",
        "example.txt",
        "done",
        aws_service="s3",
        aws_action="PutObject",
        metadata_json=json.dumps({"profile_id": 1, "region": "example-region"}),
    )
    logs.list.return_value = [event]
    assert row(service.check_permissions(1), "s3", "put").state is PermissionState.ALLOWED
    assert row(service.check_permissions(2), "s3", "put").state is PermissionState.UNKNOWN
    logs.list.return_value = [replace(event, metadata_json="{}")]
    assert row(service.check_permissions(1), "s3", "put").state is PermissionState.UNKNOWN
    logs.list.return_value = [replace(event, result=ExecutionResult.FAILURE), event]
    assert row(service.check_permissions(1), "s3", "put").state is PermissionState.UNKNOWN


def test_cli_uses_same_service_and_serializable_dto():
    from aws_connect.cli_main import _execute, build_parser

    service, *_ = build()
    app = Mock(dashboard=service)
    result = _execute(build_parser().parse_args(["dashboard", "--profile", "1"]), app)
    assert result["profile_id"] == 1
    assert len(result["features"]) == 4
    json.dumps(result)


@pytest.mark.parametrize(
    "states,expected",
    [
        ([PermissionState.ALLOWED] * 3, CapabilityState.AVAILABLE),
        ([PermissionState.DENIED] * 3, CapabilityState.UNAVAILABLE),
        ([PermissionState.ALLOWED, PermissionState.DENIED], CapabilityState.PARTIAL),
        ([PermissionState.UNKNOWN] * 3, CapabilityState.UNKNOWN),
        ([PermissionState.DENIED, PermissionState.UNKNOWN], CapabilityState.UNKNOWN),
    ],
)
def test_capability_aggregate_never_assumes_unchecked_permissions(states, expected):
    from aws_connect.application.dashboard_service import capability_state

    assert capability_state(states) is expected


def test_authentication_unavailable_does_not_probe_or_show_old_permissions():
    from aws_connect.domain.errors import CredentialValidationError

    service, _, inventory, managed, secrets, s3 = build()
    service._sessions.require_credentials.side_effect = CredentialValidationError(
        "auth.mfa_required", "expired"
    )
    result = service.check_permissions(1)
    assert all(feature.state is CapabilityState.UNKNOWN for feature in result.features)
    assert not any(gateway.method_calls for gateway in (inventory, managed, secrets, s3))
