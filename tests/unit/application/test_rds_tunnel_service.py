from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, call

import pytest

from aws_connect.application.authenticated_operation import AuthenticatedOperationCoordinator
from aws_connect.application.operations import (
    CancellationToken,
    MfaChallenge,
    OperationCancelled,
    OperationContext,
    OperationResult,
    OperationState,
)
from aws_connect.application.rds_tunnel_operation import RdsTunnelOperationCoordinator
from aws_connect.application.rds_tunnel_service import (
    PORT_FORWARD_DOCUMENT,
    RdsTunnelService,
    SaveTunnelSessionRequest,
    StartTunnelRequest,
    TunnelConnectionResult,
    TunnelSessionService,
    _ManagedTunnel,
)
from aws_connect.application.ssm_session import (
    ManagedSsmSession,
    SsmRunResult,
)
from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials
from aws_connect.domain.errors import (
    CredentialValidationError,
    PluginExecutionError,
    PortAlreadyInUseError,
    TargetNotConnectedError,
)
from aws_connect.domain.tunnel_session import TargetMode, TunnelSession


def profile() -> AwsProfile:
    return AwsProfile(
        7,
        "dev",
        "ap-northeast-2",
        "123456789012",
        "developer",
        "arn:aws:iam::123456789012:mfa/developer",
        b"a",
        b"b",
    )


def tunnel(mode: TargetMode = TargetMode.FIXED) -> TunnelSession:
    return TunnelSession(
        4,
        7,
        "dev-db",
        "db.example.internal",
        3306,
        13306,
        mode,
        "i-0123456789abcdef0" if mode is TargetMode.FIXED else None,
    )


def build_tunnel_service(*, port_available: bool = True, mode=TargetMode.FIXED):
    profiles = Mock()
    profiles.resolve.return_value = profile()
    sessions = Mock()
    credentials = PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "token")
    sessions.require_credentials.return_value = credentials
    saved = Mock()
    saved.show.return_value = tunnel(mode)
    store = Mock()
    managed = Mock()
    managed.list_online.return_value = [
        Mock(instance_id="i-0123456789abcdef0", ping_status="Online")
    ]
    runner = Mock()
    runner.run.return_value = SsmRunResult("session-test", 0)
    ports = Mock()
    ports.is_available.return_value = port_available
    clock = Mock()
    clock.now.return_value = datetime(2026, 9, 10, tzinfo=UTC)
    service = RdsTunnelService(profiles, sessions, saved, store, managed, runner, ports, clock)
    return service, sessions, store, managed, runner


def test_session_service_crud_resolves_profile_and_preserves_domain() -> None:
    profiles = Mock()
    profiles.resolve.return_value = profile()
    store = Mock()
    service = TunnelSessionService(profiles, store)
    request = SaveTunnelSessionRequest(
        "dev", "db", "db.example.internal", 5432, 15432, TargetMode.SELECT
    )
    store.create_tunnel.side_effect = lambda value: value

    created = service.create(request)

    assert created.profile_id == 7 and created.target_mode is TargetMode.SELECT
    store.list_tunnels.return_value = [created]
    assert service.list("dev") == [created]


def test_session_clone_reuses_create_with_independent_identity_and_usage_history() -> None:
    profiles = Mock()
    profiles.resolve.return_value = profile()
    store = Mock()
    service = TunnelSessionService(profiles, store)
    created_at = datetime(2026, 9, 9, tzinfo=UTC)
    source = replace(
        tunnel(),
        created_at=created_at,
        updated_at=created_at + timedelta(minutes=1),
        last_used_at=created_at + timedelta(minutes=2),
    )
    store.get_tunnel.return_value = source
    clone_time = created_at + timedelta(days=1)
    store.create_tunnel.side_effect = lambda value: replace(
        value, id=9, created_at=clone_time, updated_at=clone_time
    )

    cloned = service.clone(source.require_id(), "dev-db-copy", "dev")

    candidate = store.create_tunnel.call_args.args[0]
    assert candidate.id is None
    assert candidate.name == "dev-db-copy"
    assert candidate.profile_id == source.profile_id
    assert candidate.host == source.host
    assert candidate.remote_port == source.remote_port
    assert candidate.local_port == source.local_port
    assert candidate.target_mode is source.target_mode
    assert candidate.target_instance_id == source.target_instance_id
    assert candidate.created_at is None and candidate.updated_at is None
    assert candidate.last_used_at is None
    assert cloned.id == 9 and cloned.id != source.id
    assert cloned.last_used_at is None
    assert source.last_used_at == created_at + timedelta(minutes=2)


def test_port_collision_prevents_credentials_aws_and_plugin() -> None:
    service, sessions, store, managed, runner = build_tunnel_service(port_available=False)
    with pytest.raises(PortAlreadyInUseError, match="rds.tunnel.local_port_in_use"):
        service.start(StartTunnelRequest("dev-db", "dev"))
    sessions.require_credentials.assert_not_called()
    managed.list_online.assert_not_called()
    runner.run.assert_not_called()


@pytest.mark.parametrize(
    ("mode", "selected", "target"),
    [
        (TargetMode.FIXED, None, "i-0123456789abcdef0"),
        (TargetMode.SELECT, "i-0123456789abcdef0", "i-0123456789abcdef0"),
    ],
)
def test_fixed_and_selected_target_start_native_parameters(mode, selected, target) -> None:
    service, sessions, store, managed, runner = build_tunnel_service(mode=mode)
    result = service.start(StartTunnelRequest("dev-db", "dev", selected))

    parameters = {
        "host": ["db.example.internal"],
        "portNumber": ["3306"],
        "localPortNumber": ["13306"],
    }
    runner.run.assert_called_once_with(
        sessions.require_credentials.return_value,
        "ap-northeast-2",
        target,
        document_name=PORT_FORWARD_DOCUMENT,
        parameters=parameters,
        context=runner.run.call_args.kwargs["context"],
    )
    assert result.handle.state is OperationState.SUCCEEDED
    assert result.target_instance_id == target
    store.touch_tunnel.assert_called_once()


def test_offline_relay_is_rejected_before_start_session() -> None:
    service, sessions, store, managed, runner = build_tunnel_service()
    managed.list_online.return_value = []
    with pytest.raises(TargetNotConnectedError, match="rds.tunnel.target_not_online"):
        service.start(StartTunnelRequest("dev-db", "dev"))
    runner.run.assert_not_called()
    store.touch_tunnel.assert_not_called()


def test_mfa_resume_executes_original_tunnel_once_and_cannot_repeat() -> None:
    tunnel_service = Mock()
    tunnel_service.start.side_effect = [
        CredentialValidationError("auth.mfa_required", "test"),
        Mock(),
    ]
    refreshes = Mock()
    now = datetime(2026, 9, 10, tzinfo=UTC)
    refreshes.start_refresh.return_value = OperationResult(
        "op-1",
        OperationState.MFA_REQUIRED,
        challenge=MfaChallenge("op-1", 1, "arn:aws:iam::123456789012:mfa/developer", now),
    )
    refreshes.resume.return_value = OperationResult("op-1", OperationState.SUCCEEDED, value=Mock())
    clock = Mock()
    clock.now.return_value = now - timedelta(minutes=1)
    auth = AuthenticatedOperationCoordinator(refreshes, clock)
    coordinator = RdsTunnelOperationCoordinator(tunnel_service, auth)
    request = StartTunnelRequest("dev-db", "dev")

    started = coordinator.start(request)
    resumed = coordinator.resume(started.operation_id, "123456")
    repeated = coordinator.resume(started.operation_id, "123456")

    assert started.state is OperationState.MFA_REQUIRED
    assert resumed.state is OperationState.SUCCEEDED
    assert repeated.state is OperationState.FAILED
    assert tunnel_service.start.call_count == 2


@pytest.mark.parametrize("managed", [False, True])
def test_foreground_and_managed_coordinator_results_are_never_nested(managed: bool) -> None:
    tunnel_service = Mock()
    expected = Mock(spec=TunnelConnectionResult)
    tunnel_service.start.return_value = expected
    tunnel_service.start_managed.return_value = expected
    authentication = Mock()

    def start_long(_selector, action, context):
        return OperationResult(
            context.operation_id,
            OperationState.SUCCEEDED,
            value=action(context),
        )

    authentication.start_long.side_effect = start_long
    coordinator = RdsTunnelOperationCoordinator(tunnel_service, authentication)
    request = StartTunnelRequest("dev-db", "dev")

    result = coordinator.start_managed_operation(request) if managed else coordinator.start(request)

    assert result.value is expected
    assert not isinstance(result.value, OperationResult)
    called = tunnel_service.start_managed if managed else tunnel_service.start
    assert called.call_args.kwargs["context"].operation_id == result.operation_id


def test_gui_managed_tunnel_reports_running_and_stops_owned_process() -> None:
    profiles = Mock()
    profiles.resolve.return_value = profile()
    sessions = Mock()
    credentials = PlainCredentials("ACCESSKEYTEST0001", "test-secret-value-long", "token")
    sessions.require_credentials.return_value = credentials
    saved = Mock()
    saved.show.return_value = tunnel()
    store = Mock()
    managed_instances = Mock()
    managed_instances.list_online.return_value = [
        Mock(instance_id="i-0123456789abcdef0", ping_status="Online")
    ]
    foreground = Mock()
    managed = Mock()
    managed.start.return_value = ManagedSsmSession(
        "operation-rds", 8100, "session-rds", OperationState.RUNNING
    )
    managed.status.return_value = managed.start.return_value
    managed.stop.return_value = ManagedSsmSession(
        "operation-rds", 8100, "session-rds", OperationState.CANCELLED, 0
    )
    ports = Mock()
    ports.is_available.return_value = True
    clock = Mock()
    clock.now.return_value = datetime(2026, 9, 10, tzinfo=UTC)
    service = RdsTunnelService(
        profiles,
        sessions,
        saved,
        store,
        managed_instances,
        foreground,
        ports,
        clock,
        managed,
    )

    started = service.start_managed(StartTunnelRequest("dev-db", "dev"))
    active = service.active_tunnels()
    stopped = service.stop_all()

    assert started.handle.state is OperationState.RUNNING
    assert started.handle.operation_id == "operation-rds"
    assert started.handle.process_id == 8100
    assert active[0].handle.state is OperationState.RUNNING
    assert stopped[0].state is OperationState.CANCELLED
    managed.stop.assert_called_once_with("operation-rds")
    store.touch_tunnel.assert_called_once()


def test_stop_all_attempts_every_gui_owned_tunnel_before_reporting_failure() -> None:
    service, _sessions, _store, _managed_instances, _runner = build_tunnel_service()
    service._managed_runner = Mock()
    service._managed = {
        "operation-one": _ManagedTunnel(tunnel(), "i-0123456789abcdef0", 1, "ssm-one"),
        "operation-two": _ManagedTunnel(tunnel(), "i-0123456789abcdef0", 2, "ssm-two"),
    }
    failure = PluginExecutionError("plugin.stop.failed", "test cleanup failure")
    service.stop = Mock(  # type: ignore[method-assign]
        side_effect=[
            failure,
            Mock(),
        ]
    )

    with pytest.raises(PluginExecutionError) as raised:
        service.stop_all()

    assert raised.value is failure
    assert service.stop.call_args_list == [  # type: ignore[attr-defined]
        call("operation-one"),
        call("operation-two"),
    ]


def test_terminal_tunnel_failure_is_returned_once_then_metadata_is_released() -> None:
    service, _sessions, _store, _managed_instances, _runner = build_tunnel_service()
    failure = PluginExecutionError("plugin.exit.nonzero", "exit 8")
    service._managed_runner = Mock()
    service._managed_runner.status.return_value = ManagedSsmSession(
        "operation-rds", 5, "ssm-rds", OperationState.FAILED, 8, failure
    )
    service._managed = {
        "operation-rds": _ManagedTunnel(tunnel(), "i-0123456789abcdef0", 5, "ssm-rds")
    }

    terminal = service.active_tunnels(7)

    assert terminal[0].handle.state is OperationState.FAILED
    assert terminal[0].error is failure
    assert service.active_tunnels(7) == []


def test_tunnel_status_exception_becomes_one_terminal_result_and_releases_metadata() -> None:
    service, _sessions, _store, _managed_instances, _runner = build_tunnel_service()
    failure = PluginExecutionError("plugin.status.failed", "test")
    service._managed_runner = Mock()
    service._managed_runner.status.side_effect = failure
    service._managed = {
        "operation-rds": _ManagedTunnel(tunnel(), "i-0123456789abcdef0", 5, "ssm-rds")
    }

    terminal = service.active_tunnels(7)

    assert terminal[0].error is failure
    assert terminal[0].handle.state is OperationState.FAILED
    assert service.active_tunnels(7) == []


def test_cancelled_managed_start_stops_before_port_credentials_or_aws_checks() -> None:
    service, sessions, _store, managed_instances, _runner = build_tunnel_service()
    service._managed_runner = Mock()
    cancellation = CancellationToken()
    cancellation.cancel()

    with pytest.raises(OperationCancelled):
        service.start_managed(
            StartTunnelRequest("dev-db", "dev"),
            context=OperationContext(cancellation=cancellation),
        )

    service._ports.is_available.assert_not_called()
    sessions.require_credentials.assert_not_called()
    managed_instances.list_online.assert_not_called()
    service._managed_runner.start.assert_not_called()


def test_managed_tunnel_handle_and_progress_share_one_operation_context() -> None:
    service, _sessions, _store, _managed_instances, _runner = build_tunnel_service()
    managed_runner = Mock()
    events = []
    context = OperationContext(operation_id="rds-shared-operation", progress=events.append)

    def start(*_args, **kwargs):
        current = kwargs["context"]
        return ManagedSsmSession(current.operation_id, 8100, "session-rds", OperationState.RUNNING)

    managed_runner.start.side_effect = start
    service._managed_runner = managed_runner

    result = service.start_managed(StartTunnelRequest("dev-db", "dev"), context=context)

    assert result.handle.operation_id == context.operation_id
    assert result.handle.state is OperationState.RUNNING
    assert managed_runner.start.call_args.kwargs["context"] is context
    assert [(event.operation_id, event.phase) for event in events] == [
        (context.operation_id, "target-validation")
    ]
