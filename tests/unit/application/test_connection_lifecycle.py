from unittest.mock import Mock, call

import pytest

from aws_connect.application.connection_lifecycle import ProfileConnectionLifecycleService
from aws_connect.application.ec2_service import ExternalSessionHandle
from aws_connect.application.operations import OperationState
from aws_connect.application.rds_tunnel_service import (
    TunnelConnectionResult,
    TunnelHandle,
    TunnelOwner,
)
from aws_connect.domain.errors import ConfigurationError, PluginExecutionError
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession


def _ec2(*, error=None) -> ExternalSessionHandle:
    return ExternalSessionHandle(
        "ec2-op",
        100,
        "ssm-ec2",
        "i-0123456789abcdef0",
        7,
        OperationState.FAILED if error else OperationState.RUNNING,
        error=error,
    )


def _rds(*, error=None) -> TunnelConnectionResult:
    tunnel = TunnelSession(
        1,
        7,
        "db",
        "db.internal",
        3306,
        13306,
        TargetMode.FIXED,
        "i-0123456789abcdef0",
    )
    return TunnelConnectionResult(
        tunnel,
        "i-test",
        TunnelHandle(
            "ssm-rds",
            TunnelOwner.GUI,
            OperationState.FAILED if error else OperationState.RUNNING,
            "rds-op",
        ),
        None,
        error,
    )


def _service() -> tuple[ProfileConnectionLifecycleService, Mock, Mock, Mock]:
    profiles = Mock()
    profiles.resolve.return_value.require_id.return_value = 7
    store = Mock()
    ec2 = Mock()
    rds = Mock()
    ec2.external_sessions.return_value = [_ec2()]
    rds.active_tunnels.return_value = [_rds()]
    return ProfileConnectionLifecycleService(profiles, store, ec2, rds), store, ec2, rds


def test_active_queries_both_features_even_when_first_query_fails() -> None:
    service, _store, ec2, rds = _service()
    failure = PluginExecutionError("plugin.status.failed", "test")
    ec2.external_sessions.side_effect = failure

    with pytest.raises(PluginExecutionError) as raised:
        service.active(7)

    assert raised.value is failure
    rds.active_tunnels.assert_called_once_with(7)


def test_delete_requires_explicit_stop_choice_when_connections_are_active() -> None:
    service, store, ec2, rds = _service()

    with pytest.raises(ConfigurationError, match="profile.connections.active"):
        service.delete("dev", stop_active=False)

    service._profiles.resolve.assert_called_once_with("dev")
    ec2.stop_external_for_profile.assert_not_called()
    rds.stop_all.assert_not_called()
    store.delete.assert_not_called()


def test_delete_attempts_both_cleanup_groups_and_never_deletes_on_failure() -> None:
    service, store, ec2, rds = _service()
    failure = PluginExecutionError("plugin.stop.failed", "test")
    ec2.stop_external_for_profile.side_effect = failure
    rds.stop_all.side_effect = PluginExecutionError("session.end.failed", "test")

    with pytest.raises(PluginExecutionError) as raised:
        service.delete(7, stop_active=True)

    assert raised.value is failure
    assert ec2.method_calls[-1] == call.stop_external_for_profile(7)
    rds.stop_all.assert_called_once_with(7)
    store.delete.assert_not_called()


def test_delete_stops_all_active_connections_before_database_delete() -> None:
    service, store, ec2, rds = _service()

    service.delete(7, stop_active=True)

    ec2.stop_external_for_profile.assert_called_once_with(7)
    rds.stop_all.assert_called_once_with(7)
    store.delete.assert_called_once_with(7)
