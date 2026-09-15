from pathlib import Path
from unittest.mock import Mock, call

import pytest

from aws_connect.application.ec2_service import (
    Ec2Service,
    Ec2Target,
    Ec2TargetFilter,
    ExternalSessionHandle,
    filter_ec2_targets,
)
from aws_connect.application.operations import (
    CancellationToken,
    OperationCancelled,
    OperationContext,
    OperationState,
)
from aws_connect.application.ports import (
    Ec2Instance,
    Ec2Metadata,
    ManagedInstance,
    PluginDiagnostic,
    StartedSsmSession,
)
from aws_connect.application.ssm_session import ManagedSsmSession
from aws_connect.domain.aws_profile import AwsProfile, PlainCredentials
from aws_connect.domain.errors import (
    ApplicationError,
    AwsPermissionError,
    ConfigurationError,
    CredentialValidationError,
    PluginExecutionError,
    TargetNotConnectedError,
)


def build_service() -> tuple[Ec2Service, Mock, Mock, Mock]:
    profiles = Mock()
    profiles.resolve.return_value = AwsProfile(
        1,
        "dev",
        "ap-northeast-2",
        "123456789012",
        "developer",
        "arn:aws:iam::123456789012:mfa/developer",
        b"access",
        b"secret",
    )
    sessions = Mock()
    sessions.require_credentials.return_value = PlainCredentials(
        "ACCESSKEYTEST0001", "local-test-secret", "local-test-token"
    )
    managed = Mock()
    managed.list_online.return_value = [
        ManagedInstance("i-online", "Online", "10.0.0.1", "Amazon Linux"),
        ManagedInstance("i-offline", "ConnectionLost", "10.0.0.2", "Ubuntu"),
        ManagedInstance("i-no-name", "Online", None, "Windows"),
    ]
    metadata = Mock()
    metadata.describe.return_value = {
        "i-online": Ec2Metadata("i-online", "web", "10.0.1.1"),
        "i-no-name": Ec2Metadata("i-no-name", None, None),
    }
    plugin = Mock()
    plugin.diagnose.return_value = PluginDiagnostic(
        Path("session-manager-plugin.exe"), True, "1.2.707.0", True, True
    )
    plugin.run.return_value = 0
    managed.start_session.return_value = StartedSsmSession(
        "session-test", "wss://stream.test.invalid", "transport-token"
    )
    return Ec2Service(profiles, sessions, managed, metadata, plugin), managed, metadata, plugin


def test_list_keeps_only_online_and_enriches_optional_name() -> None:
    service, managed, metadata, plugin = build_service()

    targets = service.list_targets("dev")

    assert [target.instance_id for target in targets] == ["i-no-name", "i-online"]
    assert targets[0].name is None
    assert targets[1].name == "web"
    assert targets[1].private_ip_address == "10.0.1.1"


def test_explicit_region_and_target_filter_rules_are_application_owned() -> None:
    service, managed, _metadata, _plugin = build_service()

    targets = service.list_targets_in_region("dev", "us-east-1")
    filtered = filter_ec2_targets(targets, Ec2TargetFilter("10.0.1.1"))

    assert [target.instance_id for target in filtered] == ["i-online"]
    managed.list_online.assert_called_once_with(
        service._sessions.require_credentials.return_value, "us-east-1"
    )


def test_target_filter_rejects_non_online_status_and_invalid_region() -> None:
    service, _managed, _metadata, _plugin = build_service()

    with pytest.raises(ApplicationError, match="ec2.filter.status.unsupported"):
        filter_ec2_targets([], Ec2TargetFilter(ping_status="ConnectionLost"))
    with pytest.raises(ApplicationError, match="profile.region.invalid"):
        service.list_targets_in_region("dev", "not-a-region")


def test_describe_permission_failure_falls_back_to_ssm_information() -> None:
    service, managed, metadata, plugin = build_service()
    metadata.describe.side_effect = AwsPermissionError(
        "aws.permission.denied",
        "AccessDenied",
        aws_service="ec2",
        aws_action="DescribeInstances",
    )

    targets = service.list_targets()

    assert targets[1].name is None
    assert targets[1].private_ip_address == "10.0.0.1"


def test_list_requires_reusable_authenticated_credentials() -> None:
    service, managed, metadata, plugin = build_service()
    service._sessions.require_credentials.side_effect = CredentialValidationError(
        "auth.mfa_required", "test"
    )

    with pytest.raises(CredentialValidationError) as caught:
        service.list_targets()

    assert caught.value.message_code == "auth.mfa_required"
    managed.list_online.assert_not_called()


def test_connect_rejects_non_online_target_before_starting_session() -> None:
    service, managed, metadata, plugin = build_service()

    with pytest.raises(TargetNotConnectedError):
        service.connect("i-offline")

    managed.start_session.assert_not_called()


@pytest.mark.parametrize(
    ("diagnostic", "code"),
    [
        (PluginDiagnostic(Path("missing"), False, None, False, True), "plugin.not_found"),
        (
            PluginDiagnostic(Path("plugin"), True, "1.1.0.0", False, True),
            "plugin.version.unsupported",
        ),
        (
            PluginDiagnostic(Path("plugin"), True, "1.2.0.0", True, False),
            "plugin.environment.invalid",
        ),
    ],
)
def test_connect_rejects_unhealthy_plugin_before_start(
    diagnostic: PluginDiagnostic, code: str
) -> None:
    service, managed, metadata, plugin = build_service()
    plugin.diagnose.return_value = diagnostic

    with pytest.raises(PluginExecutionError) as caught:
        service.connect("i-online")

    assert caught.value.message_code == code
    managed.start_session.assert_not_called()


@pytest.mark.parametrize("failure", [5, KeyboardInterrupt()])
def test_connect_always_ends_ssm_session_on_failure(failure: object) -> None:
    service, managed, metadata, plugin = build_service()
    if isinstance(failure, int):
        plugin.run.return_value = failure
        expected: type[BaseException] = PluginExecutionError
    else:
        plugin.run.side_effect = failure
        expected = KeyboardInterrupt

    with pytest.raises(expected):
        service.connect("i-online")

    managed.end_session.assert_called_once_with(
        service._sessions.require_credentials.return_value,
        "ap-northeast-2",
        "session-test",
    )


def test_connect_returns_result_and_ends_session_after_normal_exit() -> None:
    service, managed, metadata, plugin = build_service()

    result = service.connect("i-online")

    assert result.instance_id == "i-online"
    assert result.session_id == "session-test"
    assert result.plugin_exit_code == 0
    managed.end_session.assert_called_once()


def test_external_connect_returns_trackable_user_owned_session() -> None:
    service, managed, metadata, plugin = build_service()
    managed_runner = Mock()
    managed_runner.start.return_value = ManagedSsmSession(
        "operation-ec2", 8200, "session-ec2", OperationState.RUNNING
    )
    managed_runner.status.return_value = managed_runner.start.return_value
    service._managed_runner = managed_runner

    started = service.connect_external("i-online", "dev")
    sessions = service.external_sessions()

    assert started.instance_id == "i-online"
    assert started.process_id == 8200
    assert sessions == [started]
    managed_runner.start.assert_called_once_with(
        service._sessions.require_credentials.return_value,
        "ap-northeast-2",
        "i-online",
        external_terminal=True,
        context=managed_runner.start.call_args.kwargs["context"],
    )


def test_external_connect_forwards_only_allowlisted_interactive_command_contract() -> None:
    service, _managed, _metadata, _plugin = build_service()
    managed_runner = Mock()
    managed_runner.start.return_value = ManagedSsmSession(
        "operation-secret", 8201, "session-secret", OperationState.RUNNING
    )
    service._managed_runner = managed_runner
    parameters = {"command": ["aws secretsmanager get-secret-value --secret-id 'db/dev'"]}

    service.connect_external(
        "i-online",
        "dev",
        document_name="AWS-StartInteractiveCommand",
        parameters=parameters,
    )

    managed_runner.start.assert_called_once_with(
        service._sessions.require_credentials.return_value,
        "ap-northeast-2",
        "i-online",
        document_name="AWS-StartInteractiveCommand",
        parameters=parameters,
        external_terminal=True,
        context=managed_runner.start.call_args.kwargs["context"],
    )


def test_external_connect_uses_explicit_feature_region() -> None:
    service, _managed, _metadata, _plugin = build_service()
    managed_runner = Mock()
    managed_runner.start.return_value = ManagedSsmSession(
        "operation-ec2", 8200, "session-ec2", OperationState.RUNNING
    )
    service._managed_runner = managed_runner

    service.connect_external("i-online", "dev", region="us-east-1")

    assert managed_runner.start.call_args.args[1] == "us-east-1"


def test_external_terminal_failure_is_reported_once_with_profile_metadata() -> None:
    service, _managed, _metadata, _plugin = build_service()
    runner = Mock()
    runner.start.return_value = ManagedSsmSession(
        "operation-ec2", 8200, "session-ec2", OperationState.RUNNING
    )
    failure = PluginExecutionError("plugin.exit.nonzero", "exit 9")
    runner.status.return_value = ManagedSsmSession(
        "operation-ec2",
        8200,
        "session-ec2",
        OperationState.FAILED,
        9,
        failure,
    )
    service._managed_runner = runner
    service.connect_external("i-online", "dev")

    terminal = service.external_sessions(1)

    assert len(terminal) == 1
    assert terminal[0].profile_id == 1
    assert terminal[0].error is failure
    assert service.external_sessions(1) == []


def test_external_status_exception_releases_metadata_and_returns_typed_terminal_handle() -> None:
    service, _managed, _metadata, _plugin = build_service()
    runner = Mock()
    runner.start.return_value = ManagedSsmSession(
        "operation-ec2", 8200, "session-ec2", OperationState.RUNNING
    )
    runner.status.side_effect = PluginExecutionError("plugin.status.failed", "test")
    service._managed_runner = runner
    service.connect_external("i-online", "dev")

    terminal = service.external_sessions(1)

    assert terminal[0].state is OperationState.FAILED
    assert terminal[0].error is runner.status.side_effect
    assert service.external_sessions(1) == []


def test_global_reaper_cleans_previous_profile_after_profile_switch() -> None:
    service, _managed, _metadata, _plugin = build_service()
    profile_a = service._profiles.resolve.return_value
    profile_b = AwsProfile(
        2,
        "staging",
        "ap-northeast-2",
        "123456789012",
        "developer-b",
        "arn:aws:iam::123456789012:mfa/developer-b",
        b"access-b",
        b"secret-b",
    )
    service._profiles.resolve.side_effect = [profile_a, profile_b]
    runner = Mock()
    runner.start.side_effect = [
        ManagedSsmSession("operation-a", 8201, "session-a", OperationState.RUNNING),
        ManagedSsmSession("operation-b", 8202, "session-b", OperationState.RUNNING),
    ]
    failure = PluginExecutionError("plugin.exit.nonzero", "exit 9")
    runner.status.side_effect = [
        ManagedSsmSession("operation-a", 8201, "session-a", OperationState.FAILED, 9, failure),
        ManagedSsmSession("operation-b", 8202, "session-b", OperationState.RUNNING),
    ]
    service._managed_runner = runner
    service.connect_external("i-online", "dev")
    service.connect_external("i-online", "staging")

    snapshots = service.reap_external_sessions()

    assert [(item.profile_id, item.state) for item in snapshots] == [
        (1, OperationState.FAILED),
        (2, OperationState.RUNNING),
    ]
    assert snapshots[0].error is failure
    assert set(service._external) == {"operation-b"}
    assert runner.status.call_args_list == [call("operation-a"), call("operation-b")]


def test_stop_external_for_profile_attempts_every_session_and_reports_typed_failure() -> None:
    service, _managed, _metadata, _plugin = build_service()
    service._external = {
        "one": Mock(profile_id=1),
        "two": Mock(profile_id=1),
    }
    failure = PluginExecutionError("plugin.stop.failed", "test")
    service.stop_external = Mock(  # type: ignore[method-assign]
        side_effect=[
            ExternalSessionHandle(
                "one", 1, "ssm-one", "i-one", 1, OperationState.FAILED, error=failure
            ),
            ExternalSessionHandle("two", 2, "ssm-two", "i-two", 1, OperationState.CANCELLED),
        ]
    )

    with pytest.raises(PluginExecutionError) as raised:
        service.stop_external_for_profile(1)

    assert raised.value is failure
    assert service.stop_external.call_count == 2  # type: ignore[attr-defined]


def test_external_handle_and_progress_share_the_caller_operation_context() -> None:
    service, _managed, _metadata, _plugin = build_service()
    runner = Mock()
    events = []
    context = OperationContext(operation_id="ec2-shared-operation", progress=events.append)

    def start(*_args, **kwargs):
        current = kwargs["context"]
        return ManagedSsmSession(current.operation_id, 8200, "session-ec2", OperationState.RUNNING)

    runner.start.side_effect = start
    service._managed_runner = runner

    handle = service.connect_external("i-online", "dev", context=context)

    assert handle.operation_id == context.operation_id
    assert handle.state is OperationState.RUNNING
    assert runner.start.call_args.kwargs["context"] is context
    assert [(event.operation_id, event.phase) for event in events] == [
        (context.operation_id, "target-validation")
    ]


def test_cancelled_external_start_stops_before_aws_or_plugin_calls() -> None:
    service, managed, _metadata, _plugin = build_service()
    cancellation = CancellationToken()
    cancellation.cancel()

    with pytest.raises(OperationCancelled):
        service.connect_external(
            "i-online", "dev", context=OperationContext(cancellation=cancellation)
        )

    managed.list_online.assert_not_called()
    managed.start_session.assert_not_called()


def test_inventory_joins_ssm_status_and_sorts_favorites_first() -> None:
    service, managed, metadata, plugin = build_service()
    inventory = Mock()
    inventory.list_inventory.return_value = [
        Ec2Instance("i-stopped", "stopped", "db", None, "Linux/UNIX"),
        Ec2Instance("i-online", "running", "web", "10.0.1.1", "Linux/UNIX"),
    ]
    managed.list_managed.return_value = [
        ManagedInstance("i-online", "Online", "10.0.1.1", "Amazon Linux")
    ]
    favorites = Mock()
    favorites.list_ec2_favorites.return_value = {"i-stopped"}
    service._inventory = inventory
    service._favorites = favorites

    targets = service.list_targets("dev")

    assert [target.instance_id for target in targets] == ["i-stopped", "i-online"]
    assert targets[0].instance_state == "stopped"
    assert targets[0].ssm_ping_status == "Offline"
    assert targets[0].favorite
    assert targets[1].platform_name == "Amazon Linux"


def test_inventory_permission_denied_degrades_to_online_without_power_capability() -> None:
    service, _managed, _metadata, _plugin = build_service()
    inventory = Mock()
    inventory.list_inventory.side_effect = AwsPermissionError(
        "aws.permission.denied", "denied", aws_service="ec2", aws_action="DescribeInstances"
    )
    service._inventory = inventory

    targets = service.list_targets("dev")

    assert {target.instance_id for target in targets} == {"i-online", "i-no-name"}
    assert all(target.instance_state is None for target in targets)
    assert all(not target.power_actions_available for target in targets)


def test_filter_supports_power_state_ssm_status_and_favorite_order() -> None:
    targets = [
        Ec2Target("i-z", "zeta", None, None, "Offline", "stopped"),
        Ec2Target("i-b", "beta", None, None, "Online", "running"),
        Ec2Target("i-a", "alpha", None, None, "Online", "running", True),
    ]

    filtered = filter_ec2_targets(
        targets, Ec2TargetFilter(ping_status="all", instance_state="running")
    )

    assert [target.instance_id for target in filtered] == ["i-a", "i-b"]


def test_set_favorite_uses_profile_region_instance_composite_key() -> None:
    service, _managed, _metadata, _plugin = build_service()
    favorites = Mock()
    service._favorites = favorites

    service.set_favorite("i-online", True, "dev", region="us-east-1")

    favorites.set_ec2_favorite.assert_called_once_with(1, "us-east-1", "i-online", True)


@pytest.mark.parametrize(
    ("method", "instance", "gateway_method"),
    [
        ("start_instance", Ec2Instance("i-test", "stopped", None, None, None), "start_instance"),
        ("reboot_instance", Ec2Instance("i-test", "running", None, None, None), "reboot_instance"),
    ],
)
def test_power_actions_enforce_state_then_request_once(
    method: str, instance: Ec2Instance, gateway_method: str
) -> None:
    service, _managed, _metadata, _plugin = build_service()
    inventory = Mock()
    inventory.list_inventory.return_value = [instance]
    power = Mock()
    service._inventory = inventory
    service._power = power

    result = getattr(service, method)("i-test", "dev")

    assert result.request_accepted
    assert result.previous_state == instance.state
    getattr(power, gateway_method).assert_called_once()


@pytest.mark.parametrize(
    ("method", "state", "code"),
    [
        ("start_instance", "running", "ec2.start.state.invalid"),
        ("reboot_instance", "stopped", "ec2.reboot.state.invalid"),
    ],
)
def test_power_actions_reject_invalid_state(method: str, state: str, code: str) -> None:
    service, _managed, _metadata, _plugin = build_service()
    inventory = Mock()
    inventory.list_inventory.return_value = [Ec2Instance("i-test", state, None, None, None)]
    power = Mock()
    service._inventory = inventory
    service._power = power

    with pytest.raises(ConfigurationError, match=code):
        getattr(service, method)("i-test", "dev")

    power.start_instance.assert_not_called()
    power.reboot_instance.assert_not_called()
